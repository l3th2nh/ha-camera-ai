"""Nhận diện ĐÓNG/MỞ cửa cuốn bằng camera — phần gắn vào Home Assistant.

Thuật toán nằm ở `door_algo.py` (thuần numpy/Pillow, mô phỏng được trên bản ghi thật). File này lo:
lấy ảnh mới nhất từ Frigate mỗi INTERVAL giây, chạy DoorTracker cho từng cửa, lưu kho mẫu xuống .storage,
báo thay đổi cho core (nhật ký/thông báo/sự kiện) và cho entity.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

import aiohttp
from PIL import Image

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import door_algo

_LOGGER = logging.getLogger(__name__)
INTERVAL = 2.0


@dataclass
class DoorConf:
    key: str
    name: str
    quad: tuple[float, ...]                 # 8 số: trên-trái, dưới-trái, dưới-phải, trên-phải (pixel 1280x720)
    band: tuple[float, float] = (0.0, 1.0)  # phần hàng dùng để đo (0=đỉnh, 1=chân) — vd chân bị xe che
    sensor: str | None = None               # cảm biến thật (tùy chọn): gieo mẫu ban đầu + hiển thị đối chiếu


def parse_doors(text: str) -> list[DoorConf]:
    """Mỗi dòng: `Tên | x1,y1,x2,y2,x3,y3,x4,y4 | dải_hàng (vd 0-1) | entity cảm biến (tùy chọn)`."""
    out: list[DoorConf] = []
    for i, line in enumerate((text or "").splitlines()):
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            quad = tuple(float(v) for v in parts[1].replace(" ", "").split(","))
            if len(quad) != 8:
                raise ValueError
            band = (0.0, 1.0)
            if len(parts) > 2 and "-" in parts[2]:
                a, b = parts[2].split("-", 1)
                band = (max(0.0, float(a)), min(1.0, float(b)))
            sensor = parts[3] if len(parts) > 3 and parts[3] else None
        except ValueError:
            _LOGGER.warning("Camera AI: bỏ qua dòng cửa không hợp lệ: %s", line)
            continue
        out.append(DoorConf(key=f"door_{i + 1}", name=parts[0], quad=quad, band=band, sensor=sensor))
    return out


def _measure(jpeg: bytes, doors: list[DoorConf]) -> tuple[str, dict]:
    im = Image.open(io.BytesIO(jpeg)).convert("RGB")
    mode = door_algo.frame_mode(im)
    return mode, {d.key: door_algo.features(door_algo.rectify(im, d.quad), d.band) for d in doors}


class DoorVision:
    def __init__(self, hass: HomeAssistant, rt, camera: str, doors: list[DoorConf],
                 on_change: Callable[[DoorConf, Any], Any]) -> None:
        self.hass, self.rt, self.camera, self.doors = hass, rt, camera, doors
        self.trackers = {d.key: door_algo.DoorTracker() for d in doors}
        self.last_change: dict[str, str | None] = {d.key: None for d in doors}
        self._on_change = on_change
        self._store = Store(hass, 1, "camera_ai.doors")
        self._busy = False
        self._unsub: Callable[[], None] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._last_save = 0.0
        self.last_error: str | None = None

    # ---------- vòng đời ----------
    async def async_start(self) -> None:
        saved = await self._store.async_load() or {}
        for d in self.doors:
            sd = saved.get(d.key) or {}
            self.trackers[d.key].load(sd)
            self.last_change[d.key] = sd.get("last_change")
        self._unsub = async_track_time_interval(self.hass, self._tick_cb, timedelta(seconds=INTERVAL))
        _LOGGER.info("Camera AI: nhận diện cửa cuốn bằng camera '%s' — %s", self.camera,
                     ", ".join(d.name for d in self.doors))

    @callback
    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        self.hass.async_create_task(self._save(force=True))

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _tick_cb(self, _now) -> None:
        """@callback bắt buộc (bài học cua_cuon): chạy trong event loop, đẩy việc ra task."""
        if not self._busy:
            self._busy = True
            self.hass.async_create_task(self._tick())

    async def async_reset_reference(self, key: str | None = None) -> None:
        """Người dùng xác nhận cửa ĐANG ĐÓNG → lấy khung hiện tại làm mẫu."""
        jpeg = await self._fetch()
        if jpeg is None:
            raise RuntimeError(self.last_error or "Không lấy được ảnh từ Frigate")
        mode, feats = await self.hass.async_add_executor_job(_measure, jpeg, self.doors)
        now = dt_util.utcnow()
        for d in self.doors:
            if key and d.key != key:
                continue
            tr = self.trackers[d.key]
            was = tr.is_open
            tr.seed(feats[d.key], mode, now.timestamp())
            if was:
                self.last_change[d.key] = now.isoformat()
                self.hass.async_create_task(self._on_change(d, tr))
        await self._save(force=True)
        self._notify_listeners()

    # ---------- đo ----------
    async def _fetch(self) -> bytes | None:
        try:
            async with async_get_clientsession(self.hass).get(
                f"{self.rt.frigate_url}/api/{self.camera}/latest.jpg", params={"h": door_algo.FRAME_H},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                if r.status != 200:
                    self.last_error = f"Frigate trả {r.status}"
                    return None
                self.last_error = None
                return await r.read()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            self.last_error = f"Không lấy được ảnh: {err}"
            return None

    async def _tick(self) -> None:
        try:
            jpeg = await self._fetch()
            if jpeg is None:
                self._notify_listeners()
                return
            mode, feats = await self.hass.async_add_executor_job(_measure, jpeg, self.doors)
            now = dt_util.utcnow()
            for d in self.doors:
                tr = self.trackers[d.key]
                sensor_closed = None
                if d.sensor and (s := self.hass.states.get(d.sensor)) is not None and \
                        s.state in ("on", "off", "open", "closed"):
                    sensor_closed = s.state in ("off", "closed")
                before = tr.is_open
                tr.update(feats[d.key], mode, now.timestamp(), sensor_closed)
                if tr.is_open != before:
                    self.last_change[d.key] = now.isoformat()
                    if before is not None:
                        _LOGGER.info("Camera AI: %s %s (camera, frac=%.2f)", d.name,
                                     "MỞ" if tr.is_open else "ĐÓNG", tr.frac)
                        self.hass.async_create_task(self._on_change(d, tr))
            await self._save()
            self._notify_listeners()
        except Exception:  # noqa: BLE001 — không để vòng đo chết vì 1 lỗi
            _LOGGER.exception("Camera AI: lỗi khi đo cửa cuốn")
        finally:
            self._busy = False

    @callback
    def _notify_listeners(self) -> None:
        for cb in list(self._listeners):
            cb()

    async def _save(self, force: bool = False) -> None:
        now = asyncio.get_running_loop().time()
        if not force and now - self._last_save < 600:
            return
        self._last_save = now
        data = {d.key: {**self.trackers[d.key].dump(), "last_change": self.last_change[d.key]} for d in self.doors}
        await self._store.async_save(data)

    # ---------- cho panel ----------
    def snapshot(self) -> list[dict]:
        out = []
        for d in self.doors:
            tr = self.trackers[d.key]
            sensor_state = None
            if d.sensor and (s := self.hass.states.get(d.sensor)) is not None:
                sensor_state = s.state
            out.append({
                "key": d.key, "name": d.name, "quad": list(d.quad), "band": list(d.band),
                "is_open": tr.is_open, "pct": tr.pct, "frac": round(tr.frac, 3), "mode": tr.mode,
                "has_ref": [m for m, v in tr.bank.items() if v],
                "n_ref": sum(len(v) for v in tr.bank.values()),
                "last_change": self.last_change[d.key], "sensor": d.sensor, "sensor_state": sensor_state,
            })
        return out
