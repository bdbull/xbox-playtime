"""Sensor entities for Xbox Play Time Tracker."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_GAMERTAGS, DOMAIN
from .coordinator import XboxPlayTimeCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: XboxPlayTimeCoordinator = hass.data[DOMAIN][entry.entry_id]
    gamertags = entry.data.get(CONF_GAMERTAGS, [])

    entities: list[SensorEntity] = []
    for kid in gamertags:
        xuid = kid["xuid"]
        gamertag = kid.get("display_name") or kid.get("gamertag") or xuid
        entities.append(XboxPlayTimeSensor(coordinator, xuid, gamertag))
        entities.append(XboxStatusSensor(coordinator, xuid, gamertag))
        entities.append(XboxCurrentGameSensor(coordinator, xuid, gamertag))

    async_add_entities(entities)


class XboxPlayTimeSensor(CoordinatorEntity[XboxPlayTimeCoordinator], SensorEntity):
    """Sensor showing daily play time in minutes."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "min"
    _attr_icon = "mdi:controller"

    def __init__(self, coordinator: XboxPlayTimeCoordinator, xuid: str, gamertag: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._xuid = xuid
        self._attr_unique_id = f"{DOMAIN}_{xuid}_play_time"
        self._attr_name = f"{gamertag} Play Time Today"

    @property
    def native_value(self) -> int | None:
        """Return play time in minutes."""
        if not self.coordinator.data or self._xuid not in self.coordinator.data:
            return None
        return self.coordinator.data[self._xuid]["play_time_minutes"]

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Return formatted play time as an attribute."""
        if not self.coordinator.data or self._xuid not in self.coordinator.data:
            return {}
        return {
            "formatted": self.coordinator.data[self._xuid]["play_time_formatted"],
            "gamertag": self.coordinator.data[self._xuid]["gamertag"],
        }


class XboxStatusSensor(CoordinatorEntity[XboxPlayTimeCoordinator], SensorEntity):
    """Sensor showing online/offline status."""

    _attr_icon = "mdi:xbox"

    def __init__(self, coordinator: XboxPlayTimeCoordinator, xuid: str, gamertag: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._xuid = xuid
        self._attr_unique_id = f"{DOMAIN}_{xuid}_status"
        self._attr_name = f"{gamertag} Xbox Status"

    @property
    def native_value(self) -> str:
        """Return online/offline status."""
        if not self.coordinator.data or self._xuid not in self.coordinator.data:
            return "Unknown"
        return "Online" if self.coordinator.data[self._xuid]["online"] else "Offline"


class XboxCurrentGameSensor(CoordinatorEntity[XboxPlayTimeCoordinator], SensorEntity):
    """Sensor showing the currently played game."""

    _attr_icon = "mdi:gamepad-variant"

    def __init__(self, coordinator: XboxPlayTimeCoordinator, xuid: str, gamertag: str) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._xuid = xuid
        self._attr_unique_id = f"{DOMAIN}_{xuid}_current_game"
        self._attr_name = f"{gamertag} Current Game"

    @property
    def native_value(self) -> str | None:
        """Return the current game title."""
        if not self.coordinator.data or self._xuid not in self.coordinator.data:
            return None
        return self.coordinator.data[self._xuid]["current_game"] or "Not playing"
