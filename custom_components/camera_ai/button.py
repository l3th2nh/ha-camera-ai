"""Nút: đồng bộ khuôn mặt từ Immich, gửi thông báo thử, tạm tắt 1 giờ."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    rt = hass.data[DOMAIN]["rt"]
    add([
        _Btn(rt, entry, "sync_faces", "Đồng bộ khuôn mặt từ Immich", "mdi:face-recognition"),
        _Btn(rt, entry, "test_notify", "Gửi thông báo thử", "mdi:bell-ring"),
        _Btn(rt, entry, "snooze", "Tạm tắt thông báo 1 giờ", "mdi:bell-sleep"),
        _Btn(rt, entry, "unsnooze", "Bật lại thông báo", "mdi:bell"),
    ])


class _Btn(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, rt, entry: ConfigEntry, key: str, name: str, icon: str) -> None:
        self._rt, self._key = rt, key
        self._attr_name, self._attr_icon = name, icon
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "Camera AI"}

    async def async_press(self) -> None:
        if self._key == "sync_faces":
            self.hass.async_create_task(self._rt.async_sync_faces())
        elif self._key == "test_notify":
            await self._rt.send("📷 Camera AI", "🔧 Thông báo thử — kênh thông báo đang hoạt động.")
        elif self._key == "snooze":
            await self._rt.async_snooze(60)
        elif self._key == "unsnooze":
            await self._rt.async_snooze(0)
