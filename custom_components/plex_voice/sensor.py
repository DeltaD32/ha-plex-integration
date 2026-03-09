"""Sensor platform for Plex Voice — now-playing info per client."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PlexVoiceCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: PlexVoiceCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        PlexNowPlayingSensor(coordinator, c["machineIdentifier"], c["name"])
        for c in coordinator.startup_client_list()
    ]
    async_add_entities(entities)

    def _on_new_client(client: dict) -> None:
        async_add_entities(
            [PlexNowPlayingSensor(coordinator, client["machineIdentifier"], client.get("name", client["machineIdentifier"]))]
        )

    coordinator.register_new_client_callback(_on_new_client)


class PlexNowPlayingSensor(CoordinatorEntity[PlexVoiceCoordinator], SensorEntity):
    """Sensor showing what is currently playing on a Plex client."""

    _attr_icon = "mdi:plex"

    def __init__(self, coordinator: PlexVoiceCoordinator, machine_id: str, client_name: str) -> None:
        super().__init__(coordinator)
        self._machine_id = machine_id
        self._attr_unique_id = f"{DOMAIN}_now_playing_{machine_id}"
        self._attr_name = f"Plex Now Playing – {client_name}"

    @property
    def _session(self) -> dict | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._machine_id)

    @property
    def native_value(self) -> str:
        session = self._session
        if not session or session.get("state") == "idle":
            return "Nothing Playing"
        return session.get("title", "Unknown")

    @property
    def extra_state_attributes(self) -> dict:
        session = self._session
        if not session:
            return {}

        attrs: dict = {
            "playback_state": session.get("state"),
            "media_type": session.get("type") or None,
            "series": session.get("grandparentTitle") or None,
            "season": session.get("parentIndex"),
            "episode": session.get("index"),
            "thumbnail": self.coordinator.get_thumbnail_url(session.get("thumb", "")),
        }

        duration = session.get("duration")
        offset = session.get("viewOffset")
        if duration and offset is not None:
            attrs["progress_pct"] = round((offset / duration) * 100, 1)
            attrs["position_s"] = round(offset / 1000)
            attrs["duration_s"] = round(duration / 1000)

        return {k: v for k, v in attrs.items() if v is not None}
