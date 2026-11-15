"""Camera AI — quản lý Frigate trong Home Assistant.

- Thông báo kèm ảnh khi Frigate có cảnh báo (người/xe...), lọc theo CHẾ ĐỘ:
  Mọi lúc · Chỉ ban đêm · Khi vắng nhà · Ban đêm hoặc vắng nhà · Tắt  (select entity + panel)
- Gắn tên người nhờ face recognition của Frigate; thư viện mặt nạp từ Immich (People có tên).
- Panel "Camera AI" trên sidebar: xem trực tiếp, nhật ký cảnh báo kèm ảnh, chế độ, tạm tắt,
  thư viện khuôn mặt, các liên kết (Frigate, Immich, NAS).
"""
from __future__ import annotations

import logging
import os

import voluptuous as vol

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, MODES, PANEL_PATH
from .core import CameraAI

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SELECT, Platform.BUTTON]
PANEL_URL = "/camera_ai/panel.js"
PANEL_VER = "1"  # tăng mỗi lần sửa panel.js (chống cache)


def _rt(hass: HomeAssistant) -> CameraAI | None:
    return hass.data.get(DOMAIN, {}).get("rt")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = hass.data.setdefault(DOMAIN, {})
    rt = CameraAI(hass, entry)
    await rt.async_start()
    data["rt"] = rt
    entry.async_on_unload(rt.async_stop)

    if not data.get("static"):
        data["static"] = True
        await hass.http.async_register_static_paths(
            [StaticPathConfig(PANEL_URL, os.path.join(os.path.dirname(__file__), "panel.js"), False)]
        )
    if PANEL_PATH not in hass.data.get(frontend.DATA_PANELS, {}):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL_PATH,
            webcomponent_name="camera-ai-panel",
            module_url=f"{PANEL_URL}?v={PANEL_VER}",
            sidebar_title="Camera AI",
            sidebar_icon="mdi:cctv",
            require_admin=False,
            config={},
        )
    if not data.get("ws"):
        data["ws"] = True
        for cmd in (ws_state, ws_set_mode, ws_snooze, ws_sync_faces, ws_test_notify, ws_clear_log):
            websocket_api.async_register_command(hass, cmd)
        _register_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if PANEL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        frontend.async_remove_panel(hass, PANEL_PATH)
    hass.data.get(DOMAIN, {}).pop("rt", None)
    return ok


# ---------------- dịch vụ ----------------
def _register_services(hass: HomeAssistant) -> None:
    async def sync_faces(call: ServiceCall) -> None:
        rt = _rt(hass)
        if rt:
            hass.async_create_task(
                rt.async_sync_faces(call.data.get("people") or None, call.data.get("per_person"))
            )

    async def snooze(call: ServiceCall) -> None:
        rt = _rt(hass)
        if rt:
            await rt.async_snooze(float(call.data.get("minutes", 60)))

    async def set_mode(call: ServiceCall) -> None:
        rt = _rt(hass)
        if rt:
            await rt.async_set_mode(call.data["mode"])

    async def test_notify(call: ServiceCall) -> None:
        rt = _rt(hass)
        if rt:
            await rt.send("📷 Camera AI", "🔧 Thông báo thử — kênh thông báo đang hoạt động.")

    hass.services.async_register(
        DOMAIN, "sync_faces", sync_faces,
        vol.Schema({vol.Optional("people"): vol.All(cv.ensure_list, [cv.string]),
                    vol.Optional("per_person"): vol.All(int, vol.Range(min=1, max=60))}),
    )
    hass.services.async_register(
        DOMAIN, "snooze", snooze,
        vol.Schema({vol.Optional("minutes", default=60): vol.All(vol.Coerce(float), vol.Range(min=0, max=1440))}),
    )
    hass.services.async_register(DOMAIN, "set_mode", set_mode, vol.Schema({vol.Required("mode"): vol.In(MODES)}))
    hass.services.async_register(DOMAIN, "test_notify", test_notify)


# ---------------- WebSocket cho panel ----------------
@websocket_api.websocket_command({vol.Required("type"): "camera_ai/state"})
@websocket_api.async_response
async def ws_state(hass, connection, msg) -> None:
    rt = _rt(hass)
    if not rt:
        connection.send_error(msg["id"], "not_ready", "Camera AI chưa sẵn sàng")
        return
    snap = await rt.snapshot()
    snap["faces"] = await rt.frigate_faces()
    connection.send_result(msg["id"], snap)


@websocket_api.websocket_command({vol.Required("type"): "camera_ai/set_mode", vol.Required("mode"): vol.In(MODES)})
@websocket_api.async_response
async def ws_set_mode(hass, connection, msg) -> None:
    rt = _rt(hass)
    if rt:
        await rt.async_set_mode(msg["mode"])
    connection.send_result(msg["id"], {"ok": bool(rt)})


@websocket_api.websocket_command({vol.Required("type"): "camera_ai/snooze", vol.Required("minutes"): vol.Coerce(float)})
@websocket_api.async_response
async def ws_snooze(hass, connection, msg) -> None:
    rt = _rt(hass)
    if rt:
        await rt.async_snooze(msg["minutes"])
    connection.send_result(msg["id"], {"ok": bool(rt)})


@websocket_api.websocket_command({vol.Required("type"): "camera_ai/sync_faces"})
@websocket_api.async_response
async def ws_sync_faces(hass, connection, msg) -> None:
    rt = _rt(hass)
    if not rt:
        connection.send_result(msg["id"], {"ok": False})
        return
    hass.async_create_task(rt.async_sync_faces())
    connection.send_result(msg["id"], {"ok": True, "started": True})


@websocket_api.websocket_command({vol.Required("type"): "camera_ai/test_notify"})
@websocket_api.async_response
async def ws_test_notify(hass, connection, msg) -> None:
    rt = _rt(hass)
    res = await rt.send("📷 Camera AI", "🔧 Thông báo thử — kênh thông báo đang hoạt động.") if rt else {"ok": False}
    connection.send_result(msg["id"], res)


@websocket_api.websocket_command({vol.Required("type"): "camera_ai/clear_log"})
@websocket_api.async_response
async def ws_clear_log(hass, connection, msg) -> None:
    rt = _rt(hass)
    if rt:
        rt.data["events"] = []
        await rt._save()  # noqa: SLF001
    connection.send_result(msg["id"], {"ok": bool(rt)})
