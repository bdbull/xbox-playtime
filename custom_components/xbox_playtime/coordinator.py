"""Data coordinator for Xbox Play Time Tracker."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Any

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_API_KEY, CONF_GAMERTAGS, DOMAIN, OPENXBL_BASE_URL, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.playtime"


class XboxPlayTimeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to poll OpenXBL presence and track play time."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
        )
        self._api_key = entry.data[CONF_API_KEY]
        self._gamertags: list[dict] = entry.data.get(CONF_GAMERTAGS, [])
        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry.entry_id}")

        # Track state per XUID
        self._state: dict[str, dict[str, Any]] = {}
        self._storage_loaded = False

    async def _async_load_storage(self) -> None:
        """Load persisted play time data from storage."""
        now = dt_util.now()
        stored = await self._store.async_load()

        for kid in self._gamertags:
            xuid = kid["xuid"]
            gt = kid.get("gamertag") or xuid
            self._state[xuid] = {
                "online": False,
                "session_start": None,
                "play_time_today": timedelta(),
                "last_reset": now.date(),
                "current_game": None,
                "gamertag": gt,
                "display_name": kid.get("display_name") or gt,
            }

        if stored and isinstance(stored, dict):
            for xuid, data in stored.items():
                if xuid not in self._state:
                    continue
                stored_date = data.get("last_reset")
                if stored_date == now.date().isoformat():
                    self._state[xuid]["play_time_today"] = timedelta(
                        seconds=data.get("play_time_seconds", 0)
                    )
                    self._state[xuid]["last_reset"] = now.date()
                    _LOGGER.debug(
                        "Restored %s play time: %ss",
                        self._state[xuid]["gamertag"],
                        data.get("play_time_seconds", 0),
                    )

        self._storage_loaded = True

    async def _async_save_storage(self) -> None:
        """Persist play time data to storage."""
        data = {}
        for xuid, state in self._state.items():
            play_time = state["play_time_today"]
            # Include current session time in the persisted value
            if state["online"] and state["session_start"]:
                play_time += dt_util.now() - state["session_start"]
            data[xuid] = {
                "play_time_seconds": int(play_time.total_seconds()),
                "last_reset": state["last_reset"].isoformat(),
            }
        await self._store.async_save(data)

    def _reset_daily_if_needed(self, xuid: str, now: datetime) -> None:
        """Reset daily play time at midnight."""
        state = self._state[xuid]
        if now.date() > state["last_reset"]:
            # If they were online at midnight, credit time up to midnight
            if state["online"] and state["session_start"]:
                midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                elapsed = midnight - state["session_start"]
                state["play_time_today"] += elapsed
                _LOGGER.debug(
                    "Midnight rollover for %s: credited %s before reset",
                    state["gamertag"],
                    elapsed,
                )
                # Start new session from midnight
                state["session_start"] = midnight

            state["play_time_today"] = timedelta()
            state["last_reset"] = now.date()

    async def _fetch_presence(self, xuids: list[str]) -> dict[str, Any]:
        """Fetch presence data from OpenXBL for given XUIDs."""
        headers = {
            "x-authorization": self._api_key,
            "Accept": "application/json",
        }

        results = {}
        async with aiohttp.ClientSession() as session:
            for xuid in xuids:
                try:
                    async with session.get(
                        f"{OPENXBL_BASE_URL}/{xuid}/presence",
                        headers=headers,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            _LOGGER.debug(
                                "OpenXBL presence for %s: %s",
                                xuid,
                                str(data)[:500],
                            )
                            # API may return a list or a dict
                            if isinstance(data, list) and len(data) > 0:
                                data = data[0]
                            if isinstance(data, dict):
                                results[xuid] = data
                        elif resp.status == 429:
                            _LOGGER.warning("OpenXBL rate limit hit")
                            raise UpdateFailed("OpenXBL rate limit exceeded")
                        else:
                            _LOGGER.warning(
                                "OpenXBL returned %s for XUID %s", resp.status, xuid
                            )
                except aiohttp.ClientError as err:
                    raise UpdateFailed(f"Error communicating with OpenXBL: {err}") from err

        return results

    def _extract_current_game(self, presence_data: dict[str, Any]) -> str | None:
        """Extract the current game title from presence data."""
        state = presence_data.get("state", "Offline")
        if state != "Online":
            return None

        devices = presence_data.get("devices", [])
        for device in devices:
            titles = device.get("titles", [])
            for title in titles:
                name = title.get("name", "")
                # Skip the Xbox dashboard/home
                if name and name.lower() not in ("home", "xbox dashboard"):
                    return name

        return None

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch presence and update play time tracking."""
        if not self._storage_loaded:
            await self._async_load_storage()

        now = dt_util.now()
        xuids = [kid["xuid"] for kid in self._gamertags]

        if not xuids:
            return {}

        presence_data = await self._fetch_presence(xuids)

        for xuid in xuids:
            if xuid not in self._state:
                continue

            self._reset_daily_if_needed(xuid, now)

            state = self._state[xuid]
            data = presence_data.get(xuid, {})

            is_online = data.get("state", "Offline") == "Online"
            current_game = self._extract_current_game(data)

            was_online = state["online"]

            if is_online and not was_online:
                # Came online
                state["session_start"] = now
                _LOGGER.debug("%s came online", state["gamertag"])

            elif not is_online and was_online:
                # Went offline - credit the session
                if state["session_start"]:
                    elapsed = now - state["session_start"]
                    state["play_time_today"] += elapsed
                    _LOGGER.debug(
                        "%s went offline. Session: %s, Total today: %s",
                        state["gamertag"],
                        elapsed,
                        state["play_time_today"],
                    )
                state["session_start"] = None

            state["online"] = is_online
            state["current_game"] = current_game

        # Persist state after every update
        await self._async_save_storage()

        # Build output data for sensors
        output = {}
        for xuid, state in self._state.items():
            play_time = state["play_time_today"]

            # Add ongoing session time if currently online
            if state["online"] and state["session_start"]:
                play_time += now - state["session_start"]

            output[xuid] = {
                "gamertag": state["gamertag"],
                "display_name": state["display_name"],
                "online": state["online"],
                "current_game": state["current_game"],
                "play_time_minutes": int(play_time.total_seconds() / 60),
                "play_time_formatted": self._format_duration(play_time),
            }

        return output

    @staticmethod
    def _format_duration(td: timedelta) -> str:
        """Format a timedelta as Xh Ym."""
        total_minutes = int(td.total_seconds() / 60)
        hours = total_minutes // 60
        minutes = total_minutes % 60
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
