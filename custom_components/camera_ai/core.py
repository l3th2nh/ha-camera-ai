"""Lõi Camera AI: nghe cảnh báo Frigate qua MQTT → lọc theo chế độ → thông báo kèm ảnh + tên người.

Nguồn sự kiện: topic `frigate/reviews` (Frigate ≥0.14). Mỗi "review" = 1 lượt có đối tượng đáng chú ý:
  type=new    → lượt mới bắt đầu
  type=update → thêm đối tượng / nhận ra mặt (sub_labels) / thêm vùng
  type=end    → lượt kết thúc
Chỉ xét severity=alert (Frigate mặc định: người, ô tô...).

Nhận ra mặt thường đến SAU (update) → nếu đã báo "Người lạ" mà giờ biết tên thì gửi lại cùng `tag`
để điện thoại THAY thông báo cũ bằng tên đúng.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time as dtime, timedelta
from typing import Any, Callable

import aiohttp

from homeassistant.components import mqtt
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import immich_faces
from .door_vision import DoorConf, DoorVision, parse_doors
from .const import (
    CONF_DOORS, CONF_DOOR_CAMERA, CONF_DOOR_NOTIFY, DEFAULT_DOORS, DEFAULT_DOOR_CAMERA,
    ACTION_SNOOZE, CONF_CAMERA_NAMES, CONF_COOLDOWN, CONF_FACES_PER_PERSON, CONF_FRIGATE_UI,
    CONF_FRIGATE_URL, CONF_IMMICH_KEY, CONF_IMMICH_APP, CONF_IMMICH_UI, CONF_IMMICH_URL, CONF_KNOWN_QUIET,
    CONF_LABELS, CONF_NAS_LINK, CONF_NIGHT_END, CONF_NIGHT_START, CONF_NOTIFY,
    DEFAULT_COOLDOWN, DEFAULT_FACES_PER_PERSON, DEFAULT_FRIGATE_UI, DEFAULT_FRIGATE_URL,
    DEFAULT_IMMICH_APP, DEFAULT_IMMICH_UI, DEFAULT_IMMICH_URL, DEFAULT_LABELS, DEFAULT_MODE, DEFAULT_NAS_LINK,
    DEFAULT_NIGHT_END, DEFAULT_NIGHT_START, DOMAIN, LABEL_VI, MAX_LOG, MODE_ALWAYS,
    MODE_NIGHT, MODE_OFF, MODES, PANEL_PATH,
)

_LOGGER = logging.getLogger(__name__)
TOPIC = "frigate/reviews"


def _parse_time(value: str | None, default: str) -> dtime:
    for v in (value, default):
        try:
            parts = [int(x) for x in str(v).split(":")[:2]]
            return dtime(parts[0], parts[1])
        except Exception:  # noqa: BLE001
            continue
    return dtime(0, 0)


def _base_label(label: str) -> str:
    return str(label).split("-")[0]


def _names(sub_labels: Any) -> list[str]:
    """sub_labels của Frigate có thể là ['Mẹ_Tâm'] hoặc [['Mẹ_Tâm', 0.9]] → ['Mẹ Tâm']."""
    out: list[str] = []
    for s in sub_labels or []:
        name = s[0] if isinstance(s, (list, tuple)) and s else s
        if isinstance(name, str) and name:
            disp = immich_faces.display_name(name)
            if disp not in out:
                out.append(disp)
    return out


class CameraAI:
    """Trạng thái + logic chạy của tích hợp (1 instance / HA)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        conf = {**entry.data, **entry.options}
        self.conf = conf
        self.frigate_url = (conf.get(CONF_FRIGATE_URL) or DEFAULT_FRIGATE_URL).rstrip("/")
        self.frigate_ui = (conf.get(CONF_FRIGATE_UI) or DEFAULT_FRIGATE_UI).rstrip("/")
        self.immich_url = (conf.get(CONF_IMMICH_URL) or DEFAULT_IMMICH_URL).rstrip("/")
        self.immich_ui = (conf.get(CONF_IMMICH_UI) or DEFAULT_IMMICH_UI).rstrip("/")
        self.immich_key = conf.get(CONF_IMMICH_KEY) or ""
        self.immich_app = conf.get(CONF_IMMICH_APP, DEFAULT_IMMICH_APP) or ""
        if self.immich_app.startswith("app.immich:"):  # v0.2.1 dùng nhầm scheme OAuth
            self.immich_app = DEFAULT_IMMICH_APP
        self.nas_link = conf.get(CONF_NAS_LINK) or DEFAULT_NAS_LINK
        self.notify_service = conf.get(CONF_NOTIFY) or ""
        self.labels = set(conf.get(CONF_LABELS) or DEFAULT_LABELS)
        self.night_start = _parse_time(conf.get(CONF_NIGHT_START), DEFAULT_NIGHT_START)
        self.night_end = _parse_time(conf.get(CONF_NIGHT_END), DEFAULT_NIGHT_END)
        self.cooldown = float(conf.get(CONF_COOLDOWN, DEFAULT_COOLDOWN) or 0)
        self.faces_per_person = int(conf.get(CONF_FACES_PER_PERSON, DEFAULT_FACES_PER_PERSON) or 15)
        self.known_quiet = bool(conf.get(CONF_KNOWN_QUIET, True))
        self.door_confs = parse_doors(conf.get(CONF_DOORS, DEFAULT_DOORS))
        self.door_camera = conf.get(CONF_DOOR_CAMERA) or DEFAULT_DOOR_CAMERA
        self.door_notify = bool(conf.get(CONF_DOOR_NOTIFY, True))
        self.vision: DoorVision | None = None
        self.camera_names = self._parse_camera_names(conf.get(CONF_CAMERA_NAMES) or "")

        self.store = Store(hass, 1, f"{DOMAIN}.log")
        self.data: dict[str, Any] = {"events": [], "mode": DEFAULT_MODE, "snooze_until": None,
                                     "face_sync": None}
        self.active: dict[str, dict] = {}          # review_id -> trạng thái lượt đang diễn ra
        self.last_notify_at: dict[str, datetime] = {}  # camera -> lần báo gần nhất
        self.notify_last: dict | None = None
        self.sync_running = False
        self.sync_progress: list[str] = []
        self._unsubs: list[Callable[[], None]] = []
        self._listeners: list[Callable[[], None]] = []

    # ---------------- vòng đời ----------------
    async def async_start(self) -> None:
        saved = await self.store.async_load()
        if saved:
            self.data.update(saved)
        if self.data.get("mode") not in MODES:
            self.data["mode"] = DEFAULT_MODE
        self._unsubs.append(await mqtt.async_subscribe(self.hass, TOPIC, self._on_mqtt, qos=0))
        self._unsubs.append(
            self.hass.bus.async_listen("mobile_app_notification_action", self._on_action)
        )
        if self.door_confs:
            self.vision = DoorVision(self.hass, self, self.door_camera, self.door_confs, self._on_door)
            await self.vision.async_start()
            self._unsubs.append(self.vision.async_stop)
        _LOGGER.info("Camera AI: nghe %s, chế độ '%s', báo qua '%s'",
                     TOPIC, self.data["mode"], self.notify_service)

    @callback
    def async_stop(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs.clear()

    async def _on_door(self, d: DoorConf, st) -> None:
        """Cửa (theo camera) vừa đổi trạng thái → nhật ký + sự kiện HA + (tùy chọn) thông báo."""
        now = dt_util.utcnow()
        entry = {
            "id": f"door-{d.key}-{int(now.timestamp())}", "type": "door", "camera": self.door_camera,
            "camera_label": d.name, "start": now.timestamp(), "end": now.timestamp(),
            "labels": [], "names": [], "event": None, "door": d.name, "door_open": st.is_open,
            "notified": False, "reason": "",
        }
        self.hass.bus.async_fire("camera_ai_door", {
            "door": d.name, "key": d.key, "open": st.is_open, "frac": round(st.frac, 3)})
        if self.door_notify and not self.snooze_until:
            icon = "🔓" if st.is_open else "🔒"
            verb = "vừa MỞ" if st.is_open else "đã ĐÓNG"
            res = await self.send(f"📷 {d.name}", f"{icon} {d.name} {verb} lúc {dt_util.now():%H:%M} (nhận diện bằng camera)",
                                  # tag RIÊNG mỗi lượt: chung tag thì iPhone cho tin "đóng" ĐÈ MẤT tin "mở"
                                  {"tag": f"camera_ai_{d.key}_{int(now.timestamp())}", "url": f"/{PANEL_PATH}", "clickAction": f"/{PANEL_PATH}",
                                   "image": f"/api/camera_proxy/camera.{self.door_camera}"})
            entry["notified"] = bool(res.get("ok"))
        else:
            entry["reason"] = "tắt thông báo cửa" if not self.door_notify else "đang tạm tắt"
        self.data["events"].insert(0, entry)
        del self.data["events"][MAX_LOG:]
        await self._save()
        self._changed()

    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _changed(self) -> None:
        for cb in list(self._listeners):
            cb()

    async def _save(self) -> None:
        await self.store.async_save(self.data)

    # ---------------- tiện ích ----------------
    @staticmethod
    def _parse_camera_names(raw: str) -> dict[str, str]:
        out = {}
        for part in raw.replace("\n", ",").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() and v.strip():
                    out[k.strip()] = v.strip()
        return out

    def camera_label(self, cam: str) -> str:
        return self.camera_names.get(cam) or cam.replace("_", " ").title()

    @property
    def mode(self) -> str:
        return self.data.get("mode", DEFAULT_MODE)

    async def async_set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(mode)
        self.data["mode"] = mode
        await self._save()
        self._changed()

    @property
    def snooze_until(self) -> datetime | None:
        v = self.data.get("snooze_until")
        dt = dt_util.parse_datetime(v) if v else None
        return dt if dt and dt > dt_util.utcnow() else None

    async def async_snooze(self, minutes: float) -> None:
        self.data["snooze_until"] = (
            (dt_util.utcnow() + timedelta(minutes=minutes)).isoformat() if minutes > 0 else None
        )
        await self._save()
        self._changed()

    def is_night(self, now: datetime | None = None) -> bool:
        t = dt_util.as_local(now or dt_util.utcnow()).time()
        s, e = self.night_start, self.night_end
        return (s <= t or t < e) if s > e else (s <= t < e)

    def mode_allows(self) -> tuple[bool, str]:
        m = self.mode
        if m == MODE_OFF:
            return False, "chế độ Tắt"
        if self.snooze_until:
            return False, "đang tạm tắt"
        if m == MODE_ALWAYS:
            return True, ""
        if m == MODE_NIGHT:
            night = self.is_night()
            return night, "" if night else "ban ngày"
        return True, ""

    # ---------------- sự kiện Frigate ----------------
    @callback
    def _on_mqtt(self, msg) -> None:
        """BẮT BUỘC @callback (xem bài học cua_cuon): chạy trong event loop, đẩy việc nặng ra task."""
        try:
            payload = json.loads(msg.payload)
        except (ValueError, TypeError):
            return
        self.hass.async_create_task(self._handle_review(payload))

    async def _handle_review(self, payload: dict) -> None:
        kind = payload.get("type")
        after = payload.get("after") or {}
        rid = after.get("id")
        if not rid or after.get("severity") != "alert":
            return
        cam = after.get("camera", "")
        d = after.get("data") or {}
        labels = sorted({_base_label(o) for o in d.get("objects") or []})
        names = _names(d.get("sub_labels"))
        detections = d.get("detections") or []

        st = self.active.get(rid)
        if st is None:
            st = {"notified": False, "names": [], "labels": [], "entry": None}
            self.active[rid] = st
            entry = {
                "id": rid, "camera": cam, "camera_label": self.camera_label(cam),
                "start": after.get("start_time"), "end": None,
                "labels": labels, "names": names, "event": detections[0] if detections else None,
                "notified": False, "reason": "",
            }
            self.data["events"].insert(0, entry)
            del self.data["events"][MAX_LOG:]
            st["entry"] = entry
        entry = st["entry"]
        entry["labels"], entry["names"] = labels, names or entry.get("names", [])
        if detections and not entry.get("event"):
            entry["event"] = detections[0]

        new_names = [n for n in names if n not in st["names"]]
        st["names"] = list(dict.fromkeys(st["names"] + names))

        if kind == "end":
            entry["end"] = after.get("end_time")
            self.active.pop(rid, None)
        else:
            relevant = bool(self.labels.intersection(labels))
            if not st["notified"]:
                if relevant:
                    ok, reason = self._should_notify(cam)
                    if ok:
                        await self._notify_review(entry)
                        st["notified"] = True
                        entry["notified"] = True
                        entry["reason"] = ""
                    else:
                        entry["reason"] = reason
                else:
                    entry["reason"] = "loại đối tượng không chọn"
            elif new_names:
                # đã báo rồi, giờ mới nhận ra mặt → cập nhật thông báo (cùng tag)
                await self._notify_review(entry, update=True)

        await self._save()
        self._changed()

    def _should_notify(self, cam: str) -> tuple[bool, str]:
        ok, reason = self.mode_allows()
        if not ok:
            return False, reason
        last = self.last_notify_at.get(cam)
        if last and self.cooldown and (dt_util.utcnow() - last).total_seconds() < self.cooldown:
            return False, "trong thời gian chờ"
        return True, ""

    def describe(self, entry: dict) -> tuple[str, bool]:
        """(câu mô tả, có_người_lạ)."""
        names, labels = entry.get("names") or [], entry.get("labels") or []
        parts: list[str] = []
        stranger = False
        if "person" in labels:
            if names:
                parts.append("👤 " + ", ".join(names))
            else:
                parts.append("⚠️ Người lạ")
                stranger = True
        for lb in labels:
            if lb != "person":
                parts.append(LABEL_VI.get(lb, lb))
        return (" · ".join(parts) or "Có chuyển động đáng chú ý"), stranger

    async def _notify_review(self, entry: dict, update: bool = False) -> dict:
        desc, stranger = self.describe(entry)
        start = entry.get("start")
        when = dt_util.as_local(dt_util.utc_from_timestamp(start)) if start else dt_util.now()
        msg = f"{desc} — {when:%H:%M}"
        event_id = entry.get("event")
        data: dict[str, Any] = {
            "tag": f"camera_ai_{entry['id']}",
            "group": "camera_ai",
            "url": f"/{PANEL_PATH}",
            "clickAction": f"/{PANEL_PATH}",
            "actions": [
                {"action": "URI", "title": "Xem clip",
                 "uri": f"{self.frigate_ui}/review?id={entry['id']}"},
                {"action": ACTION_SNOOZE, "title": "Tạm tắt 1 giờ"},
            ],
        }
        if event_id:
            data["image"] = f"/api/frigate/notifications/{event_id}/snapshot.jpg"
        quiet = update or (self.known_quiet and not stranger)
        if quiet:
            data["push"] = {"interruption-level": "passive"}
            data["channel"] = "Camera AI - người quen"
            data["importance"] = "low"
        elif stranger:
            data["push"] = {"interruption-level": "time-sensitive"}
            data["channel"] = "Camera AI - người lạ"
            data["importance"] = "high"
            data["priority"] = "high"
            data["ttl"] = 0
        result = await self.send(f"📷 {entry.get('camera_label') or entry.get('camera')}", msg, data)
        if result.get("ok") and not update:
            self.last_notify_at[entry.get("camera", "")] = dt_util.utcnow()
        return result

    async def send(self, title: str, message: str, data: dict | None = None) -> dict:
        """Gửi notify, blocking=True để bắt được lỗi (bài học cua_cuon)."""
        now = dt_util.utcnow().isoformat()
        if not self.notify_service:
            self.notify_last = {"at": now, "ok": False, "err": "Chưa chọn dịch vụ thông báo"}
            return self.notify_last
        svc = self.notify_service.split(".")[-1]
        try:
            await asyncio.wait_for(
                self.hass.services.async_call(
                    "notify", svc, {"title": title, "message": message, "data": data or {}},
                    blocking=True,
                ),
                timeout=30,
            )
            self.notify_last = {"at": now, "ok": True, "msg": message}
        except Exception as err:  # noqa: BLE001
            self.notify_last = {"at": now, "ok": False, "msg": message,
                                "err": f"{type(err).__name__}: {err}"}
            _LOGGER.error("Camera AI: gửi thông báo thất bại qua %s: %s", svc, err)
        return self.notify_last

    @callback
    def _on_action(self, event: Event) -> None:
        if event.data.get("action") == ACTION_SNOOZE:
            self.hass.async_create_task(self.async_snooze(60))

    # ---------------- khuôn mặt ----------------
    async def frigate_faces(self) -> dict[str, int]:
        session = async_get_clientsession(self.hass)
        try:
            lib = await immich_faces.FrigateFaces(session, self.frigate_url).list()
            return {immich_faces.display_name(k): len(v) for k, v in lib.items()}
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Camera AI: không đọc được thư viện mặt Frigate: %s", err)
            return {}

    async def async_sync_faces(self, only: list[str] | None = None,
                               per_person: int | None = None) -> dict:
        if self.sync_running:
            return {"ok": False, "err": "Đang đồng bộ, chờ lượt trước xong"}
        if not self.immich_key:
            return {"ok": False, "err": "Chưa nhập Immich API key"}
        self.sync_running = True
        self.sync_progress = []
        self._changed()

        async def progress(text: str) -> None:
            self.sync_progress.append(text)
            del self.sync_progress[:-50]
            self._changed()

        started = dt_util.utcnow().isoformat()
        try:
            res, done = await immich_faces.sync_faces(
                async_get_clientsession(self.hass), self.immich_url, self.immich_key,
                self.frigate_url, per_person=per_person or self.faces_per_person,
                only=only, done=self.data.get("face_done") or {}, progress=progress,
            )
            self.data["face_done"] = done
            self.data["immich_people"] = {**self.data.get("immich_people", {}), **res.ids}
            summary = {"at": started, "ok": True, "people": res.people, "added": res.added,
                       "skipped": res.skipped, "failed": res.failed, "errors": res.errors[:10]}
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError) as err:
            summary = {"at": started, "ok": False, "err": f"{type(err).__name__}: {err}"}
        finally:
            self.sync_running = False
        self.data["face_sync"] = summary
        await self._save()
        self._changed()
        return summary

    # ---------------- cho panel ----------------
    async def snapshot(self) -> dict:
        allowed, reason = self.mode_allows()
        snooze = self.snooze_until
        return {
            "mode": self.mode,
            "modes": MODES,
            "allowed_now": allowed,
            "blocked_reason": reason,
            "is_night": self.is_night(),
            "night": f"{self.night_start:%H:%M}–{self.night_end:%H:%M}",
            "snooze_until": snooze.isoformat() if snooze else None,
            "labels": sorted(self.labels),
            "cooldown": self.cooldown,
            "events": self.data.get("events", [])[:150],
            "links": {"frigate": self.frigate_ui, "immich": self.immich_ui, "immich_app": self.immich_app,
                      "nas": self.nas_link},
            "notify_service": self.notify_service,
            "notify_last": self.notify_last,
            "face_sync": self.data.get("face_sync"),
            "sync_running": self.sync_running,
            "sync_progress": self.sync_progress[-8:],
            "faces_per_person": self.faces_per_person,
            "immich_people": self.data.get("immich_people", {}),
            "doors": self.vision.snapshot() if self.vision else [],
            "door_camera": self.door_camera,
            "door_error": self.vision.last_error if self.vision else None,
        }
