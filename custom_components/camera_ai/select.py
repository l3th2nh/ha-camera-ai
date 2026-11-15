"""Select: chế độ thông báo (Mọi lúc / Chỉ ban đêm / Khi vắng nhà / Ban đêm hoặc vắng nhà / Tắt)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MODES


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    add([ModeSelect(hass.data[DOMAIN]["rt"], entry)])


class ModeSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_name = "Chế độ thông báo"
    _attr_icon = "mdi:bell-cog"
    _attr_options = MODES

    def __init__(self, rt, entry: ConfigEntry) -> None:
        self._rt = rt
        self._attr_unique_id = f"{entry.entry_id}_mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Camera AI",
            "manufacturer": "Frigate + Immich",
        }

    @property
    def current_option(self) -> str:
        return self._rt.mode

    @property
    def extra_state_attributes(self) -> dict:
        allowed, reason = self._rt.mode_allows()
        snooze = self._rt.snooze_until
        return {
            "dang_bao": allowed,
            "ly_do_chan": reason or None,
            "ban_dem": self._rt.is_night(),
            "vang_nha": self._rt.is_away(),
            "tam_tat_den": snooze.isoformat() if snooze else None,
        }

    async def async_select_option(self, option: str) -> None:
        await self._rt.async_set_mode(option)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._rt.add_listener(self._refresh))

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
