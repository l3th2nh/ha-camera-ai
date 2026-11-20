"""Config flow Camera AI: kết nối Frigate/Immich + kênh thông báo; Options: lọc, giờ đêm..."""
from __future__ import annotations

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ALL_LABELS, CONF_CAMERA_NAMES, CONF_COOLDOWN, CONF_FACES_PER_PERSON, CONF_FRIGATE_UI,
    CONF_FRIGATE_URL, CONF_IMMICH_KEY, CONF_IMMICH_APP, CONF_IMMICH_UI, CONF_IMMICH_URL, CONF_KNOWN_QUIET, CONF_DOORS, CONF_DOOR_CAMERA, CONF_DOOR_NOTIFY,
    CONF_LABELS, CONF_NAS_LINK, CONF_NIGHT_END, CONF_NIGHT_START, CONF_NOTIFY,
    DEFAULT_COOLDOWN, DEFAULT_FACES_PER_PERSON, DEFAULT_FRIGATE_UI, DEFAULT_FRIGATE_URL,
    DEFAULT_IMMICH_APP, DEFAULT_IMMICH_UI, DEFAULT_DOORS, DEFAULT_DOOR_CAMERA, DEFAULT_IMMICH_URL, DEFAULT_LABELS, DEFAULT_NAS_LINK, DEFAULT_NIGHT_END,
    DEFAULT_NIGHT_START, DOMAIN, LABEL_VI,
)


def _notify_selector(hass) -> selector.SelectSelector:
    services = sorted((hass.services.async_services().get("notify") or {}).keys())
    return selector.SelectSelector(selector.SelectSelectorConfig(
        options=[f"notify.{s}" for s in services],
        mode=selector.SelectSelectorMode.DROPDOWN, custom_value=True,
    ))


def _text(password: bool = False) -> selector.TextSelector:
    return selector.TextSelector(selector.TextSelectorConfig(
        type=selector.TextSelectorType.PASSWORD if password else selector.TextSelectorType.TEXT
    ))


def _connection_schema(hass, cur: dict) -> dict:
    return {
        vol.Required(CONF_FRIGATE_URL, default=cur.get(CONF_FRIGATE_URL, DEFAULT_FRIGATE_URL)): _text(),
        vol.Required(CONF_FRIGATE_UI, default=cur.get(CONF_FRIGATE_UI, DEFAULT_FRIGATE_UI)): _text(),
        vol.Required(CONF_NOTIFY, default=cur.get(CONF_NOTIFY, "")): _notify_selector(hass),
        vol.Optional(CONF_IMMICH_URL, default=cur.get(CONF_IMMICH_URL, DEFAULT_IMMICH_URL)): _text(),
        vol.Optional(CONF_IMMICH_KEY, default=cur.get(CONF_IMMICH_KEY, "")): _text(password=True),
        vol.Optional(CONF_IMMICH_UI, default=cur.get(CONF_IMMICH_UI, DEFAULT_IMMICH_UI)): _text(),
        vol.Optional(CONF_IMMICH_APP, default=cur.get(CONF_IMMICH_APP, DEFAULT_IMMICH_APP)): _text(),
        vol.Optional(CONF_NAS_LINK, default=cur.get(CONF_NAS_LINK, DEFAULT_NAS_LINK)): _text(),
    }


async def _check_frigate(hass, url: str) -> bool:
    try:
        async with async_get_clientsession(hass).get(
            f"{url.rstrip('/')}/api/version", timeout=aiohttp.ClientTimeout(total=10)
        ) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


class CameraAIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            if await _check_frigate(self.hass, user_input[CONF_FRIGATE_URL]):
                return self.async_create_entry(title="Camera AI", data=user_input)
            errors["base"] = "cannot_connect"
        return self.async_show_form(
            step_id="user", data_schema=vol.Schema(_connection_schema(self.hass, user_input or {})),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return CameraAIOptionsFlow(config_entry)


class CameraAIOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        cur = {**self._entry.data, **self._entry.options}
        schema = {
            **_connection_schema(self.hass, cur),
            vol.Optional(CONF_LABELS, default=cur.get(CONF_LABELS, DEFAULT_LABELS)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=k, label=LABEL_VI[k]) for k in ALL_LABELS],
                    multiple=True, mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Optional(CONF_NIGHT_START, default=cur.get(CONF_NIGHT_START, DEFAULT_NIGHT_START)): selector.TimeSelector(),
            vol.Optional(CONF_NIGHT_END, default=cur.get(CONF_NIGHT_END, DEFAULT_NIGHT_END)): selector.TimeSelector(),
            vol.Optional(CONF_COOLDOWN, default=float(cur.get(CONF_COOLDOWN, DEFAULT_COOLDOWN))): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=3600, step=10, unit_of_measurement="giây",
                                              mode=selector.NumberSelectorMode.BOX)
            ),
            vol.Optional(CONF_KNOWN_QUIET, default=bool(cur.get(CONF_KNOWN_QUIET, True))): selector.BooleanSelector(),
            vol.Optional(CONF_CAMERA_NAMES, default=cur.get(CONF_CAMERA_NAMES, "san_them=Sân - Thềm")): _text(),
            vol.Optional(CONF_DOOR_NOTIFY, default=bool(cur.get(CONF_DOOR_NOTIFY, True))): selector.BooleanSelector(),
            vol.Optional(CONF_DOOR_CAMERA, default=cur.get(CONF_DOOR_CAMERA, DEFAULT_DOOR_CAMERA)): _text(),
            vol.Optional(CONF_DOORS, default=cur.get(CONF_DOORS, DEFAULT_DOORS)): selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            ),
            vol.Optional(CONF_FACES_PER_PERSON, default=float(cur.get(CONF_FACES_PER_PERSON, DEFAULT_FACES_PER_PERSON))): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=60, step=1, mode=selector.NumberSelectorMode.BOX)
            ),
        }
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
