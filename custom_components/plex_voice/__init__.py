"""Plex Voice Integration for Home Assistant.

Allows browsing and playing Plex media via voice assistant and UI.
Supports conversational voice flows: "play Star Wars on the living room TV"
"""

from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
import voluptuous as vol
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import PlexVoiceCoordinator
from .helpers import get_machine_id_for_entity, get_plex_player_entities

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Plex Voice from a config entry."""
    coordinator = PlexVoiceCoordinator(hass, entry)

    # async_setup fetches libraries and pre-populates known clients.
    # Failures here mean the server is unreachable — surface as ConfigEntryNotReady.
    try:
        await coordinator.async_setup()
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to connect to Plex: {err}") from err

    # First session poll via DataUpdateCoordinator; also raises ConfigEntryNotReady on failure.
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    from .intents import async_setup_intents
    await async_setup_intents(hass, coordinator)

    _async_register_services(hass)

    # Reload the entry whenever options are saved so the coordinator picks up
    # any URL/token/name changes immediately.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when the options flow saves new settings."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


# ---------------------------------------------------------------------------
# plex_voice.play service — lets any LLM or script trigger real Plex playback
# ---------------------------------------------------------------------------

SERVICE_PLAY = "play"
SERVICE_PLAY_SCHEMA = vol.Schema(
    {
        vol.Required("title"): cv.string,
        vol.Optional("entity_id"): cv.string,
        vol.Optional("media_type"): vol.In(["movie", "show", "music"]),
    }
)


def _async_register_services(hass: HomeAssistant) -> None:
    """Register plex_voice services (idempotent — safe to call on each entry load)."""
    if hass.services.has_service(DOMAIN, SERVICE_PLAY):
        return

    async def _handle_play(call: ServiceCall) -> None:
        title: str = call.data["title"]
        entity_id: str | None = call.data.get("entity_id")
        media_type_filter: str | None = call.data.get("media_type")

        # Find the first available coordinator
        coordinators: list[PlexVoiceCoordinator] = list(hass.data.get(DOMAIN, {}).values())
        if not coordinators:
            _LOGGER.error("plex_voice.play: no coordinator found")
            return
        coordinator = coordinators[0]

        # Search Plex
        results = await coordinator.search(title, media_type=media_type_filter)
        if not results:
            _LOGGER.warning("plex_voice.play: no results found for '%s'", title)
            return

        item = results[0]
        media_key = item.get("ratingKey", "")
        item_type = item.get("type", "movie")

        if not media_key:
            _LOGGER.error("plex_voice.play: search result has no ratingKey")
            return

        # Resolve target device
        if not entity_id:
            players = get_plex_player_entities(hass)
            if not players:
                _LOGGER.error("plex_voice.play: no Plex player entities found")
                return
            entity_id = players[0][0]

        machine_id = get_machine_id_for_entity(hass, entity_id)
        if machine_id:
            success = await coordinator.play_on_client(machine_id, media_key, item_type)
            if success:
                _LOGGER.info(
                    "plex_voice.play: playing '%s' on %s (machine_id=%s)",
                    item.get("title"), entity_id, machine_id,
                )
                return
            _LOGGER.warning(
                "plex_voice.play: play_on_client failed for %s, falling back to service call",
                machine_id,
            )

        # Fallback: call media_player.play_media on the entity directly
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": entity_id,
                "media_content_id": media_key,
                "media_content_type": item_type,
            },
            blocking=False,
        )

    hass.services.async_register(
        DOMAIN, SERVICE_PLAY, _handle_play, schema=SERVICE_PLAY_SCHEMA
    )
