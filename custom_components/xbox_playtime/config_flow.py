"""Config flow for Xbox Play Time Tracker."""

from __future__ import annotations

from typing import Any

import logging

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_API_KEY, CONF_GAMERTAGS, DOMAIN, OPENXBL_BASE_URL

_LOGGER = logging.getLogger(__name__)


async def validate_api_key(api_key: str) -> bool:
    """Validate the OpenXBL API key by making a test request."""
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"x-authorization": api_key, "Accept": "application/json"}
            async with session.get(
                f"{OPENXBL_BASE_URL}/account", headers=headers
            ) as resp:
                body = await resp.text()
                _LOGGER.debug(
                    "OpenXBL validation: status=%s body=%s", resp.status, body[:500]
                )
                return resp.status == 200
    except Exception as err:
        _LOGGER.error("OpenXBL validation failed: %s", err)
        return False


async def resolve_gamertag(api_key: str, gamertag: str) -> dict | None:
    """Resolve a gamertag to XUID via OpenXBL."""
    async with aiohttp.ClientSession() as session:
        headers = {"X-Authorization": api_key, "Accept": "application/json"}
        async with session.get(
            f"{OPENXBL_BASE_URL}/search/{gamertag}", headers=headers
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            if data and "people" in data and len(data["people"]) > 0:
                person = data["people"][0]
                return {
                    "xuid": person.get("xuid"),
                    "gamertag": person.get("gamertag"),
                    "display_name": person.get("displayName", person.get("gamertag")),
                }
            return None


class XboxPlayTimeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Xbox Play Time Tracker."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._api_key: str = ""
        self._gamertags: list[dict] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> XboxPlayTimeOptionsFlow:
        """Get the options flow."""
        return XboxPlayTimeOptionsFlow(config_entry)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the API key step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            if await validate_api_key(api_key):
                self._api_key = api_key
                return await self.async_step_gamertags()
            errors["base"] = "invalid_api_key"

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}),
            errors=errors,
        )

    async def async_step_gamertags(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle adding gamertags."""
        errors: dict[str, str] = {}

        if user_input is not None:
            gamertag_input = user_input.get("gamertag", "").strip()

            if gamertag_input:
                resolved = await resolve_gamertag(self._api_key, gamertag_input)
                if resolved:
                    self._gamertags.append(resolved)
                else:
                    errors["base"] = "gamertag_not_found"

            if not gamertag_input and self._gamertags:
                return self.async_create_entry(
                    title="Xbox Play Time",
                    data={
                        CONF_API_KEY: self._api_key,
                        CONF_GAMERTAGS: self._gamertags,
                    },
                )

            if not gamertag_input and not self._gamertags:
                errors["base"] = "no_gamertags"

        added = ", ".join(g["gamertag"] for g in self._gamertags) if self._gamertags else "None yet"

        return self.async_show_form(
            step_id="gamertags",
            data_schema=vol.Schema(
                {vol.Optional("gamertag", default=""): str}
            ),
            errors=errors,
            description_placeholders={"added_gamertags": added},
        )


class XboxPlayTimeOptionsFlow(OptionsFlow):
    """Handle options flow for Xbox Play Time Tracker."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        if user_input is not None:
            gamertags = self._config_entry.data.get(CONF_GAMERTAGS, [])

            if user_input.get("action") == "add" and user_input.get("gamertag"):
                api_key = self._config_entry.data[CONF_API_KEY]
                resolved = await resolve_gamertag(api_key, user_input["gamertag"])
                if resolved:
                    gamertags = [*gamertags, resolved]

            if user_input.get("action") == "remove" and user_input.get("remove_gamertag"):
                gamertags = [
                    g for g in gamertags
                    if g["gamertag"] != user_input["remove_gamertag"]
                ]

            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data={**self._config_entry.data, CONF_GAMERTAGS: gamertags},
            )
            return self.async_create_entry(title="", data={})

        current_gamertags = self._config_entry.data.get(CONF_GAMERTAGS, [])
        gamertag_names = [g["gamertag"] for g in current_gamertags]

        schema_dict: dict[vol.Marker, Any] = {
            vol.Required("action", default="add"): vol.In(["add", "remove"]),
            vol.Optional("gamertag", default=""): str,
        }

        if gamertag_names:
            schema_dict[vol.Optional("remove_gamertag")] = vol.In(gamertag_names)

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema_dict),
        )
