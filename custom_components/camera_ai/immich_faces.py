"""Đồng bộ khuôn mặt từ Immich → thư viện mặt Frigate.

Immich đã gom ảnh theo người và gắn tên (People). Với mỗi người có tên:
  1. tìm ảnh có người đó (POST /api/search/metadata {personIds}),
  2. lấy toạ độ khuôn mặt của đúng người đó trong ảnh (GET /api/faces?id=<asset>),
  3. tải ảnh preview, cắt vùng mặt (nới lề), chọn N mặt TO NHẤT/rõ nhất,
  4. đẩy lên Frigate: POST /api/faces/<tên>/register (multipart "file").

Đã đồng bộ ảnh nào thì ghi lại (asset_id + face_id) để lần sau không nạp trùng.
Module này KHÔNG phụ thuộc Home Assistant (chỉ aiohttp + Pillow) → chạy thử độc lập được.
"""
from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import quote

import aiohttp
from PIL import Image

_LOGGER = logging.getLogger(__name__)

MIN_FACE_PX = 70        # mặt nhỏ hơn (cạnh ngắn, trên ảnh preview) → bỏ, quá mờ để học
MARGIN = 0.35           # nới lề quanh khung mặt (35% mỗi phía) để Frigate tự dò lại mặt
OUT_MAX = 480           # cạnh dài tối đa của ảnh gửi lên Frigate
SCAN_ASSETS = 60        # mỗi người quét tối đa ngần này ảnh để chọn ra N mặt đẹp nhất


@dataclass
class SyncResult:
    people: int = 0
    added: int = 0
    skipped: int = 0
    failed: int = 0
    per_person: dict[str, int] = field(default_factory=dict)
    ids: dict[str, str] = field(default_factory=dict)   # tên → Immich person id
    errors: list[str] = field(default_factory=list)


def frigate_name(name: str) -> str:
    """Tên trong thư viện Frigate (Frigate tự đổi khoảng trắng → '_')."""
    return name.strip().replace(" ", "_")


def display_name(frigate_label: str | None) -> str | None:
    return frigate_label.replace("_", " ") if frigate_label else frigate_label


class ImmichClient:
    def __init__(self, session: aiohttp.ClientSession, url: str, api_key: str) -> None:
        self._s = session
        self._url = url.rstrip("/")
        self._h = {"x-api-key": api_key, "Accept": "application/json"}

    async def _json(self, method: str, path: str, **kw):
        async with self._s.request(
            method, f"{self._url}/api{path}", headers=self._h,
            timeout=aiohttp.ClientTimeout(total=30), **kw,
        ) as r:
            r.raise_for_status()
            return await r.json()

    async def ping(self) -> bool:
        data = await self._json("GET", "/server/ping")
        return data.get("res") == "pong"

    async def named_people(self) -> list[dict]:
        data = await self._json("GET", "/people", params={"size": 1000, "withHidden": "false"})
        return [p for p in data.get("people", []) if (p.get("name") or "").strip()]

    async def person_assets(self, person_id: str, limit: int = SCAN_ASSETS) -> list[str]:
        body = {"personIds": [person_id], "type": "IMAGE", "size": limit, "page": 1}
        data = await self._json("POST", "/search/metadata", json=body)
        return [a["id"] for a in data.get("assets", {}).get("items", [])]

    async def faces(self, asset_id: str) -> list[dict]:
        return await self._json("GET", "/faces", params={"id": asset_id})

    async def preview(self, asset_id: str) -> bytes:
        async with self._s.get(
            f"{self._url}/api/assets/{asset_id}/thumbnail", params={"size": "preview"},
            headers={"x-api-key": self._h["x-api-key"]},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            r.raise_for_status()
            return await r.read()


class FrigateFaces:
    def __init__(self, session: aiohttp.ClientSession, url: str) -> None:
        self._s = session
        self._url = url.rstrip("/")

    async def list(self) -> dict[str, list[str]]:
        async with self._s.get(f"{self._url}/api/faces", timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            return await r.json()

    async def register(self, name: str, jpeg: bytes) -> tuple[bool, str]:
        n = quote(frigate_name(name), safe="")
        async with self._s.post(
            f"{self._url}/api/faces/{n}/create", timeout=aiohttp.ClientTimeout(total=20)
        ):
            pass  # đã tồn tại cũng được
        form = aiohttp.FormData()
        form.add_field("file", jpeg, filename="immich.jpg", content_type="image/jpeg")
        async with self._s.post(
            f"{self._url}/api/faces/{n}/register", data=form,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as r:
            try:
                body = await r.json(content_type=None)
            except Exception:  # noqa: BLE001
                body = {"message": await r.text()}
            ok = r.status == 200 and body.get("success", True) is not False
            return ok, str(body.get("message", r.status))


def crop_face(img_bytes: bytes, face: dict) -> bytes | None:
    """Cắt vùng mặt (có nới lề, vuông) từ ảnh preview. None nếu mặt quá nhỏ."""
    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    W, H = img.size
    fw, fh = face.get("imageWidth") or W, face.get("imageHeight") or H
    sx, sy = W / fw, H / fh
    x1, y1 = face["boundingBoxX1"] * sx, face["boundingBoxY1"] * sy
    x2, y2 = face["boundingBoxX2"] * sx, face["boundingBoxY2"] * sy
    w, h = x2 - x1, y2 - y1
    if min(w, h) < MIN_FACE_PX:
        return None
    side = max(w, h) * (1 + 2 * MARGIN)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    box = (
        int(max(0, cx - side / 2)), int(max(0, cy - side / 2)),
        int(min(W, cx + side / 2)), int(min(H, cy + side / 2)),
    )
    out = img.crop(box)
    out.thumbnail((OUT_MAX, OUT_MAX))
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=92)
    return buf.getvalue()


async def sync_faces(
    session: aiohttp.ClientSession,
    immich_url: str,
    api_key: str,
    frigate_url: str,
    per_person: int = 15,
    only: list[str] | None = None,
    done: dict[str, list[str]] | None = None,
    progress: Callable[[str], Awaitable[None] | None] | None = None,
) -> tuple[SyncResult, dict[str, list[str]]]:
    """Đồng bộ. `done` = {person_id: [face_id đã nạp]} (lưu lại giữa các lần chạy).

    Mỗi người đảm bảo có tối đa `per_person` mặt từ Immich trong Frigate; người đã đủ thì bỏ qua.
    """
    immich = ImmichClient(session, immich_url, api_key)
    frigate = FrigateFaces(session, frigate_url)
    done = {k: list(v) for k, v in (done or {}).items()}
    res = SyncResult()

    async def say(msg: str) -> None:
        _LOGGER.info("camera_ai faces: %s", msg)
        if progress:
            r = progress(msg)
            if asyncio.iscoroutine(r):
                await r

    people = await immich.named_people()
    if only:
        wanted = {o.strip().casefold() for o in only}
        people = [p for p in people if p["name"].strip().casefold() in wanted]
    res.people = len(people)
    # Số ảnh THỰC CÓ trong thư viện Frigate (gồm cả ảnh gán tay ở tab Train) → không nạp dư
    try:
        library = await frigate.list()
    except Exception as err:  # noqa: BLE001
        raise RuntimeError(f"Không đọc được thư viện mặt Frigate: {err}") from err

    for person in people:
        pid, name = person["id"], person["name"].strip()
        res.ids[name] = pid
        have = done.setdefault(pid, [])
        in_frigate = len(library.get(frigate_name(name), []))
        need = per_person - in_frigate
        if need <= 0:
            res.skipped += 1
            res.per_person[name] = in_frigate
            continue
        await say(f"{name}: cần thêm {need} mặt")
        try:
            assets = await immich.person_assets(pid)
        except Exception as err:  # noqa: BLE001
            res.failed += 1
            res.errors.append(f"{name}: không lấy được ảnh ({err})")
            continue

        # Gom ứng viên: (diện tích mặt, asset, face) của ĐÚNG người này
        cands: list[tuple[float, str, dict]] = []
        for aid in assets:
            try:
                for f in await immich.faces(aid):
                    if (f.get("person") or {}).get("id") != pid or f["id"] in have:
                        continue
                    area = (f["boundingBoxX2"] - f["boundingBoxX1"]) * (
                        f["boundingBoxY2"] - f["boundingBoxY1"]
                    ) / max(1, (f.get("imageWidth") or 1) * (f.get("imageHeight") or 1))
                    cands.append((area, aid, f))
            except Exception:  # noqa: BLE001
                continue
        cands.sort(key=lambda c: c[0], reverse=True)

        added = 0
        for _area, aid, f in cands:
            if added >= need:
                break
            try:
                jpeg = crop_face(await immich.preview(aid), f)
                if jpeg is None:
                    continue
                ok, msg = await frigate.register(name, jpeg)
            except Exception as err:  # noqa: BLE001
                ok, msg = False, str(err)
            if ok:
                have.append(f["id"])
                added += 1
            else:
                res.failed += 1
                if len(res.errors) < 30:
                    res.errors.append(f"{name}: {msg}")
        res.added += added
        res.per_person[name] = in_frigate + added
        await say(f"{name}: đã nạp {added} (tổng {in_frigate + added})")

    return res, done
