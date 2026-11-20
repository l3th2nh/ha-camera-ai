"""sensor: % mở của cửa cuốn (ước lượng từ camera)."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add: AddEntitiesCallback) -> None:
    rt = hass.data[DOMAIN]["rt"]
    if rt.vision:
        add([DoorPct(rt, entry, d) for d in rt.door_confs])


class DoorPct(SensorEntity):
    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:garage-open-variant"

    def __init__(self, rt, entry: ConfigEntry, door) -> None:
        self._rt, self._door = rt, door
        self._attr_name = f"{door.name} độ mở (camera)"
        self._attr_unique_id = f"{entry.entry_id}_{door.key}_pct"
        self._attr_device_info = {"identifiers": {(DOMAIN, entry.entry_id)}, "name": "Camera AI"}

    @property
    def available(self) -> bool:
        st = self._rt.vision.trackers[self._door.key]
        return st.is_open is not None and self._rt.vision.last_error is None

    @property
    def native_value(self) -> int:
        return self._rt.vision.trackers[self._door.key].pct

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._rt.vision.add_listener(self._refresh))

    @callback
    def _refresh(self) -> None:
        # chỉ ghi khi giá trị đổi (tránh spam recorder mỗi 2s)
        val = self.native_value
        if val != getattr(self, "_last", None):
            self._last = val
            self.async_write_ha_state()
