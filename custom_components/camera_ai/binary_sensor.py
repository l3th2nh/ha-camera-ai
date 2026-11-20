"""binary_sensor: cửa cuốn đóng/mở nhận diện bằng camera (device_class garage_door)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    rt = hass.data[DOMAIN]["rt"]
    if rt.vision:
        add([DoorBinary(rt, entry, d) for d in rt.door_confs])


class DoorBinary(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.GARAGE_DOOR

    def __init__(self, rt, entry: ConfigEntry, door) -> None:
        self._rt, self._door = rt, door
        self._attr_name = f"{door.name} (camera)"
        self._attr_unique_id = f"{entry.entry_id}_{door.key}_open"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "Camera AI"}

    @property
    def _st(self):
        return self._rt.vision.trackers[self._door.key]

    @property
    def available(self) -> bool:
        return self._st.is_open is not None and self._rt.vision.last_error is None

    @property
    def is_on(self) -> bool | None:
        return self._st.is_open

    @property
    def extra_state_attributes(self) -> dict:
        st = self._st
        return {
            "phan_tram_mo": st.pct,
            "do_lech": round(st.frac, 3),
            "che_do_anh": "đêm (hồng ngoại)" if st.mode == "night" else "ngày",
            "doi_luc": self._rt.vision.last_change[self._door.key],
            "so_mau": sum(len(v) for v in st.bank.values()),
            "cam_bien_doi_chieu": self._door.sensor,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._rt.vision.add_listener(self._refresh))

    @callback
    def _refresh(self) -> None:
        # đo mỗi 2s nhưng chỉ ghi khi có thay đổi đáng kể (tránh phình lịch sử/recorder)
        st = self._st
        key = (st.is_open, st.pct, st.mode, self.available)
        if key != getattr(self, "_last", None):
            self._last = key
            self.async_write_ha_state()
