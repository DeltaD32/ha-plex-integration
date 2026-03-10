"""Shared helpers for the Plex Voice integration."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def get_machine_id_for_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    """Resolve an HA media_player entity_id to its Plex machineIdentifier.

    - Official HA Plex integration (platform='plex'): unique_id IS the machine ID.
    - This integration (platform='plex_voice'): unique_id = 'plex_voice_{machine_id}'.
    """
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if not entry:
        return None
    if entry.platform == "plex":
        return entry.unique_id
    if entry.platform == DOMAIN:
        prefix = f"{DOMAIN}_"
        uid = entry.unique_id or ""
        if uid.startswith(prefix) and not uid.startswith(f"{prefix}browser"):
            return uid[len(prefix):]
    return None


def get_plex_player_entities(hass: HomeAssistant) -> list[tuple[str, str]]:
    """Return [(entity_id, friendly_name)] for all real Plex player entities.

    Includes entities from the official HA Plex integration and this one,
    but excludes the virtual library-browser entity which can't play media.
    """
    ent_reg = er.async_get(hass)
    players: list[tuple[str, str]] = []
    for entry in ent_reg.entities.values():
        if entry.domain != "media_player":
            continue
        if entry.platform not in ("plex", DOMAIN):
            continue
        uid = entry.unique_id or ""
        if uid.startswith(f"{DOMAIN}_browser"):
            continue
        state = hass.states.get(entry.entity_id)
        name = (
            (state.attributes.get("friendly_name") if state else None)
            or entry.original_name
            or entry.entity_id
        )
        players.append((entry.entity_id, name))
    return sorted(players, key=lambda p: p[1].lower())
