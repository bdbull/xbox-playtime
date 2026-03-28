"""Config flow for Xbox Play Time Tracker."""

from __future__ import annotations

from typing import Any

import logging
from urllib.parse import quote

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_API_KEY, CONF_GAMERTAGS, DOMAIN, OPENXBL_BASE_URL

_LOGGER = logging.getLogger(__name__)


async def validate_api_key(api_key: str) -> bool:
    """Validate the OpenXBL API key by making a test request."""
    headers = {"x-authorization": api_key, "Accept": "application/json"}
    urls = [
        "https://xbl.io/api/v2/account",
        "https://api.xbl.io/v2/account",
    ]
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url, headers=headers) as resp:
                    body = await resp.text()
                    if resp.status == 200:
                        return True
                    _LOGGER.warning(
                        "OpenXBL API key validation failed at %s: status=%s body=%s",
                        url, resp.status, body[:500],
                    )
            except Exception as err:
                _LOGGER.error("OpenXBL validation request to %s failed: %s", url, err)
                continue
    _LOGGER.error("All OpenXBL validation endpoints failed. Check your API key.")
    return False


async def resolve_gamertag(api_key: str, gamertag: str) -> dict | None:
    """Resolve a gamertag to XUID via OpenXBL. Tries multiple endpoints."""
    encoded_gt = quote(gamertag, safe="")
    headers = {"x-authorization": api_key, "Accept": "application/json"}

    # OpenXBL docs are inconsistent - try all known endpoints
    urls = [
        f"https://xbl.io/api/v2/search/{encoded_gt}",
        f"https://xbl.io/api/v2/player/gamertag/{encoded_gt}",
        f"https://api.xbl.io/v2/search/{encoded_gt}",
        f"https://api.xbl.io/v2/player/gamertag/{encoded_gt}",
    ]

    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                _LOGGER.debug("OpenXBL trying: %s", url)
                async with session.get(url, headers=headers) as resp:
                    body = await resp.text()
                    if resp.status != 200:
                        _LOGGER.warning(
                            "OpenXBL %s returned status %s: %s",
                            url, resp.status, body[:500],
                        )
                        continue
                    data = await resp.json(content_type=None)

                    # Unwrap content envelope if present
                    if isinstance(data, dict) and "content" in data:
                        data = data["content"]

                    # Handle search endpoint (returns {people: [...]})
                    if isinstance(data, dict) and "people" in data:
                        people = data["people"]
                        if people and len(people) > 0:
                            person = people[0]
                            gt = person.get("gamertag") or gamertag
                            return {
                                "xuid": person.get("xuid"),
                                "gamertag": gt,
                                "display_name": person.get("displayName") or gt,
                            }

                    # Handle direct profile endpoint (returns flat dict)
                    if isinstance(data, dict) and "xuid" in data:
                        gt = data.get("gamertag") or gamertag
                        return {
                            "xuid": data.get("xuid"),
                            "gamertag": gt,
                            "display_name": data.get("displayName") or gt,
                        }

                    # Handle list response
                    if isinstance(data, list) and len(data) > 0:
                        person = data[0]
                        gt = person.get("gamertag") or gamertag
                        return {
                            "xuid": person.get("xuid"),
                            "gamertag": gt,
                            "display_name": person.get("displayName") or gt,
                        }

                    # Got 200 but couldn't parse - log the structure
                    _LOGGER.error(
                        "OpenXBL returned 200 but response structure not recognized: %s",
                        str(data)[:500],
                    )
            except Exception as err:
                _LOGGER.error("OpenXBL %s failed: %s", url, err)
                continue

    _LOGGER.error(
        "Could not resolve gamertag '%s' from any OpenXBL endpoint", gamertag
    )
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
