"""Binary sensor platform for Plex Voice — active playback per client."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PlexVoiceCoordinator

_ACTIVE_STATES = {"playing", "paused", "buffering"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PlexVoiceCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PlexClientActiveSensor(coordinator, c["machineIdentifier"], c["name"])
        for c in coordinator.startup_client_list()
    ]
    async_add_entities(entities)

    def _on_new_client(client: dict) -> None:
        async_add_entities(
            [PlexClientActiveSensor(coordinator, client["machineIdentifier"], client.get("name", client["machineIdentifier"]))]
        )

    coordinator.register_new_client_callback(_on_new_client)


class PlexClientActiveSensor(CoordinatorEntity[PlexVoiceCoordinator], BinarySensorEntity):
    """True when a Plex client has an active (playing or paused) session."""

    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_icon = "mdi:plex"

    def __init__(self, coordinator: PlexVoiceCoordinator, machine_id: str, client_name: str) -> None:
        super().__init__(coordinator)
        self._machine_id = machine_id
        self._attr_unique_id = f"{DOMAIN}_active_{machine_id}"
        self._attr_name = f"Plex Active – {client_name}"

    @property
    def is_on(self) -> bool:
        if not self.coordinator.data:
            return False
        session = self.coordinator.data.get(self._machine_id)
        return session is not None and session.get("state") in _ACTIVE_STATES
