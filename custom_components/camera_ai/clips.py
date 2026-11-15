"""Xem lại clip Frigate NGAY TRONG HA (không phải mở giao diện Frigate).

- `list_reviews`: lấy danh sách lượt (review) từ Frigate API, kèm link ảnh thu nhỏ (qua proxy của tích hợp
  Frigate) và link clip ĐÃ KÝ (signed path, hết hạn sau 6h) để thẻ <video> phát được mà không cần header auth.
- `ClipView` (/api/camera_ai/clip/<review_id>.mp4): tải clip từ Frigate → **chuẩn hoá cho iPhone** bằng ffmpeg
  của HA (video giữ nguyên `-c:v copy`, âm thanh → AAC 44.1kHz, `+faststart`) → lưu đệm → trả bằng FileResponse
  (hỗ trợ HTTP Range — Safari iOS bắt buộc). Tapo phát G.711 8kHz → AAC 8kHz làm iOS lỗi "Media failed to decode".
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import timedelta

import aiohttp
from aiohttp import web

from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import immich_faces
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

RID_RE = re.compile(r"^[0-9]+\.[0-9]+-[a-z0-9]+$")
CACHE_MAX_BYTES = 1_500_000_000      # ~1.5GB đệm trên HA, quá thì xoá clip cũ nhất
SIGN_TTL = timedelta(hours=6)


def cache_dir(hass: HomeAssistant) -> str:
    return hass.config.path("camera_ai_cache")


def _names(sub_labels) -> list[str]:
    out = []
    for s in sub_labels or []:
        n = s[0] if isinstance(s, (list, tuple)) and s else s
        if isinstance(n, str) and n:
            d = immich_faces.display_name(n)
            if d not in out:
                out.append(d)
    return out


async def list_reviews(hass: HomeAssistant, rt, before: float | None, severity: str,
                       limit: int = 40, camera: str | None = None) -> list[dict]:
    params: dict = {"limit": limit}
    if before:
        params["before"] = before
    if severity in ("alert", "detection"):
        params["severity"] = severity
    if camera:
        params["cameras"] = camera
    session = async_get_clientsession(hass)
    async with session.get(f"{rt.frigate_url}/api/review", params=params,
                           timeout=aiohttp.ClientTimeout(total=20)) as r:
        r.raise_for_status()
        items = await r.json()
    out = []
    for it in items:
        rid, cam = it.get("id", ""), it.get("camera", "")
        if not RID_RE.match(rid):
            continue
        d = it.get("data") or {}
        out.append({
            "id": rid,
            "camera": cam,
            "camera_label": rt.camera_label(cam),
            "start": it.get("start_time"),
            "end": it.get("end_time"),
            "severity": it.get("severity"),
            "labels": sorted({str(o).split("-")[0] for o in d.get("objects") or []}),
            "names": _names(d.get("sub_labels")),
            "zones": d.get("zones") or [],
            "thumb": f"/api/frigate/notifications/{rid}/{cam}/review_thumbnail.webp",
            # chỉ ký khi lượt đã kết thúc (đang diễn ra thì clip chưa đủ)
            "clip": async_sign_path(hass, f"/api/{DOMAIN}/clip/{rid}.mp4", SIGN_TTL)
            if it.get("end_time") else None,
        })
    return out


def _prune(folder: str) -> None:
    try:
        files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".mp4")]
    except FileNotFoundError:
        return
    files.sort(key=os.path.getmtime)
    total = sum(os.path.getsize(f) for f in files)
    while files and total > CACHE_MAX_BYTES:
        f = files.pop(0)
        total -= os.path.getsize(f)
        os.remove(f)


class ClipView(HomeAssistantView):
    """Phục vụ clip đã chuẩn hoá. requires_auth=True — dùng link đã ký từ `list_reviews`."""

    url = f"/api/{DOMAIN}/clip/{{review_id}}.mp4"
    name = f"api:{DOMAIN}:clip"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, request: web.Request, review_id: str) -> web.StreamResponse:
        if not RID_RE.match(review_id):
            return web.Response(status=400, text="review id không hợp lệ")
        rt = self.hass.data.get(DOMAIN, {}).get("rt")
        if rt is None:
            return web.Response(status=503, text="Camera AI chưa sẵn sàng")
        folder = cache_dir(self.hass)
        path = os.path.join(folder, f"{review_id}.mp4")
        lock = self._locks.setdefault(review_id, asyncio.Lock())
        async with lock:
            if not await self.hass.async_add_executor_job(os.path.exists, path):
                err = await self._fetch(rt, review_id, folder, path)
                if err:
                    return web.Response(status=502, text=err)
        self._locks.pop(review_id, None)
        await self.hass.async_add_executor_job(os.utime, path, None)
        return web.FileResponse(path, headers={"Cache-Control": "private, max-age=86400"})

    async def _fetch(self, rt, review_id: str, folder: str, path: str) -> str | None:
        await self.hass.async_add_executor_job(lambda: os.makedirs(folder, exist_ok=True))
        raw = path + ".src"
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(f"{rt.frigate_url}/api/review/{review_id}/clip.mp4",
                                   timeout=aiohttp.ClientTimeout(total=180)) as r:
                if r.status != 200:
                    return f"Frigate trả {r.status}"
                body = await r.read()  # clip vài MB–vài chục MB; ghi đĩa ở luồng phụ (không chặn event loop)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            return f"Không tải được clip từ Frigate: {err}"

        def _write() -> None:
            with open(raw, "wb") as f:
                f.write(body)

        await self.hass.async_add_executor_job(_write)
        del body

        tmp = path + ".part.mp4"
        ts = path + ".part.ts"
        # Bước 1 — MP4 → MPEG-TS (copy): bỏ avcC cũ. Tapo đổi độ phân giải giữa chừng (2K → 720p) mà go2rtc
        #   vẫn giữ SPS cũ trong header → avcC ghi 2560px nhưng khung hình 1280px. Máy tính đọc SPS trong luồng
        #   nên vẫn chiếu được; iPhone (VideoToolbox) tin avcC → CHỈ CÓ TIẾNG, KHÔNG CÓ HÌNH.
        # Bước 2 — TS → MP4: avcC dựng lại từ SPS thật trong luồng; audio → AAC 44.1kHz; +faststart.
        steps = [
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", raw,
             "-c", "copy", "-bsf:v", "h264_mp4toannexb", "-f", "mpegts", ts],
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", ts,
             "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy",
             "-c:a", "aac", "-ar", "44100", "-b:a", "64k",
             "-movflags", "+faststart", tmp],
        ]
        ok = False
        try:
            for cmd in steps:
                proc = await asyncio.create_subprocess_exec(
                    *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
                if proc.returncode != 0:
                    _LOGGER.warning("Camera AI: ffmpeg chuẩn hoá clip %s lỗi: %s", review_id,
                                    stderr.decode(errors="ignore")[-300:])
                    break
            else:
                ok = await self.hass.async_add_executor_job(
                    lambda: os.path.exists(tmp) and os.path.getsize(tmp) > 1024)
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Camera AI: không chạy được ffmpeg (%s) — trả clip gốc", err)

        def _finish() -> None:
            if os.path.exists(ts):
                os.remove(ts)
            if ok:
                os.replace(tmp, path)
                os.remove(raw)
            else:
                if os.path.exists(tmp):
                    os.remove(tmp)
                os.replace(raw, path)  # dự phòng: clip gốc (máy tính vẫn xem được)
            _prune(folder)

        await self.hass.async_add_executor_job(_finish)
        _LOGGER.debug("Camera AI: đệm clip %s (%s) lúc %s", review_id,
                      "chuẩn hoá" if ok else "gốc", time.strftime("%H:%M:%S"))
        return None
