"""Plex Voice Integration for Home Assistant.

Allows browsing and playing Plex media via voice assistant and UI.
Supports conversational voice flows: "play Star Wars on the living room TV"
"""

from __future__ import annotations

import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import DOMAIN, PLATFORMS
from .coordinator import PlexVoiceCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Plex Voice from a config entry."""
    coordinator = PlexVoiceCoordinator(hass, entry)

    try:
        await coordinator.async_setup()
    except Exception as err:
        raise ConfigEntryNotReady(f"Failed to connect to Plex: {err}") from err

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register intent handlers for voice assistant
    from .intents import async_setup_intents
    await async_setup_intents(hass, coordinator)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
