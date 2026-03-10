"""Intent handlers for Plex Voice assistant integration.

Conversational voice flow:
  "Play Star Wars on the living room TV"
  → search Plex
  → if ambiguous (movie vs show): ask
  → if multiple matches: list and ask which one
  → confirm and play

Sessions are scoped per conversation so two simultaneous voice interactions
(e.g. two Alexa devices) don't trample each other's state.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent, entity_registry as er, device_registry as dr, area_registry as ar

from .helpers import get_machine_id_for_entity, get_plex_player_entities

from .const import (
    DOMAIN,
    INTENT_PLAY_MEDIA,
    INTENT_CLARIFY_TYPE,
    INTENT_CLARIFY_TITLE,
    INTENT_CLARIFY_DEVICE,
    PLEX_TYPE_MOVIE,
    PLEX_TYPE_SHOW,
    PLEX_TYPE_MUSIC,
    PLEX_TYPE_ALBUM,
    PLEX_TYPE_TRACK,
    SESSION_KEY,
)
from .coordinator import PlexVoiceCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_intents(hass: HomeAssistant, coordinator: PlexVoiceCoordinator) -> None:
    """Register all Plex Voice intent handlers."""
    intent.async_register(hass, PlexPlayMediaIntent(hass, coordinator))
    intent.async_register(hass, PlexClarifyTypeIntent(hass, coordinator))
    intent.async_register(hass, PlexClarifyTitleIntent(hass, coordinator))
    intent.async_register(hass, PlexClarifyDeviceIntent(hass, coordinator))
    _LOGGER.info("Plex Voice: registered voice intent handlers")


# ---------------------------------------------------------------------------
# Per-conversation session helpers
# ---------------------------------------------------------------------------


def _conversation_key(intent_obj: intent.Intent) -> str:
    """Return a stable key that isolates each concurrent conversation."""
    return (
        getattr(intent_obj, "conversation_id", None)
        or getattr(intent_obj.context, "user_id", None)
        or "default"
    )


def _get_session(hass: HomeAssistant, key: str) -> dict:
    sessions: dict = hass.data.setdefault(SESSION_KEY, {})
    return sessions.setdefault(key, {})


def _clear_session(hass: HomeAssistant, key: str) -> None:
    hass.data.get(SESSION_KEY, {}).pop(key, None)


def _find_player_entity(hass: HomeAssistant, location_hint: str) -> str | None:
    """Match a spoken location hint to a Plex media_player entity.

    Checks (in order) against: entity friendly name, HA device name,
    HA area name, and entity_id. This allows "living room" to match a
    Plex player whose device is assigned to the Living Room area in HA.
    """
    hint = location_hint.lower().strip()

    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)
    area_reg = ar.async_get(hass)

    for entity_id, _ in get_plex_player_entities(hass):
        state = hass.states.get(entity_id)
        if not state:
            continue

        # 1. Friendly name from state
        friendly_name = state.attributes.get("friendly_name", "").lower()
        if hint in friendly_name or hint in entity_id.lower():
            return entity_id

        # 2. HA device name + area name via registries
        entry = ent_reg.async_get(entity_id)
        if entry and entry.device_id:
            device = dev_reg.async_get(entry.device_id)
            if device:
                device_name = (device.name_by_user or device.name or "").lower()
                if hint in device_name:
                    return entity_id

                area_id = entry.area_id or device.area_id
                if area_id:
                    area = area_reg.async_get_area(area_id)
                    if area and hint in area.name.lower():
                        return entity_id

    return None


# ---------------------------------------------------------------------------
# Intent handlers
# ---------------------------------------------------------------------------


class PlexPlayMediaIntent(intent.IntentHandler):
    """Handle: 'Play {title} [from plex] [on {room}]'."""

    intent_type = INTENT_PLAY_MEDIA
    slot_schema = {
        vol.Required("title"): intent.non_empty_string,
        vol.Optional("room"): str,
    }

    def __init__(self, hass: HomeAssistant, coordinator: PlexVoiceCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = intent_obj.slots
        title: str = slots.get("title", {}).get("value", "")
        room: str = slots.get("room", {}).get("value", "")
        conv_key = _conversation_key(intent_obj)

        if not title:
            response = intent_obj.create_response()
            response.async_set_speech("What would you like to play from Plex?")
            return response

        results = await self.coordinator.search(title)

        if not results:
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I couldn't find anything called {title} on your Plex server."
            )
            return response

        movies = [r for r in results if r.get("type") == PLEX_TYPE_MOVIE]
        shows = [r for r in results if r.get("type") == PLEX_TYPE_SHOW]
        music = [
            r for r in results if r.get("type") in (PLEX_TYPE_MUSIC, PLEX_TYPE_ALBUM, PLEX_TYPE_TRACK)
        ]

        session = _get_session(self.hass, conv_key)
        session.update({"title_query": title, "room": room, "movies": movies, "shows": shows, "music": music})

        # Only movies
        if movies and not shows and not music:
            if len(movies) == 1:
                return await _confirm_and_play(self.hass, self.coordinator, intent_obj, movies[0], room, conv_key)
            session["media_type"] = PLEX_TYPE_MOVIE
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {len(movies)} movies matching {title}: "
                f"{_format_list([m.get('title', '?') for m in movies])}. Which one?"
            )
            return response

        # Only shows
        if shows and not movies and not music:
            if len(shows) == 1:
                return await _confirm_and_play(self.hass, self.coordinator, intent_obj, shows[0], room, conv_key)
            session["media_type"] = PLEX_TYPE_SHOW
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {len(shows)} shows matching {title}: "
                f"{_format_list([s.get('title', '?') for s in shows])}. Which one?"
            )
            return response

        # Only music
        if music and not movies and not shows:
            if len(music) == 1:
                return await _confirm_and_play(self.hass, self.coordinator, intent_obj, music[0], room, conv_key)
            session["media_type"] = PLEX_TYPE_MUSIC
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {len(music)} music results for {title}: "
                f"{_format_list([m.get('title', '?') for m in music])}. Which one?"
            )
            return response

        # Mixed results — ask what type
        types_found = []
        if movies:
            types_found.append("a movie")
        if shows:
            types_found.append("a show")
        if music:
            types_found.append("music")
        response = intent_obj.create_response()
        response.async_set_speech(
            f"I found {title} as {_format_list(types_found)} on Plex. "
            f"Would you like the {' or the '.join(t.replace('a ', '') for t in types_found)}?"
        )
        return response


class PlexClarifyTypeIntent(intent.IntentHandler):
    """Handle follow-up: 'movie', 'show', or 'music' after ambiguous search."""

    intent_type = INTENT_CLARIFY_TYPE
    slot_schema = {
        vol.Required("media_type"): intent.non_empty_string,
    }

    def __init__(self, hass: HomeAssistant, coordinator: PlexVoiceCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        conv_key = _conversation_key(intent_obj)
        media_type_str: str = intent_obj.slots.get("media_type", {}).get("value", "").lower()

        session = _get_session(self.hass, conv_key)
        if not session.get("title_query"):
            response = intent_obj.create_response()
            response.async_set_speech(
                "I'm not sure what you're referring to. Try saying 'play something on Plex' first."
            )
            return response

        if "movie" in media_type_str or "film" in media_type_str:
            media_type = PLEX_TYPE_MOVIE
            results = session.get("movies", [])
        elif "music" in media_type_str or "song" in media_type_str or "track" in media_type_str:
            media_type = PLEX_TYPE_MUSIC
            results = session.get("music", [])
        else:
            media_type = PLEX_TYPE_SHOW
            results = session.get("shows", [])

        session["media_type"] = media_type
        room = session.get("room", "")

        if not results:
            _clear_session(self.hass, conv_key)
            response = intent_obj.create_response()
            response.async_set_speech(f"I couldn't find any {media_type_str} matching that title.")
            return response

        if len(results) == 1:
            return await _confirm_and_play(self.hass, self.coordinator, intent_obj, results[0], room, conv_key)

        titles = _format_list([r.get("title", "?") for r in results])
        response = intent_obj.create_response()
        response.async_set_speech(f"I found these: {titles}. Which one would you like?")
        return response


class PlexClarifyTitleIntent(intent.IntentHandler):
    """Handle follow-up: user picks a specific title from a list."""

    intent_type = INTENT_CLARIFY_TITLE
    slot_schema = {
        vol.Required("title"): intent.non_empty_string,
    }

    def __init__(self, hass: HomeAssistant, coordinator: PlexVoiceCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        conv_key = _conversation_key(intent_obj)
        chosen_title: str = intent_obj.slots.get("title", {}).get("value", "").lower()

        session = _get_session(self.hass, conv_key)
        media_type = session.get("media_type", PLEX_TYPE_MOVIE)
        room = session.get("room", "")

        if media_type == PLEX_TYPE_MOVIE:
            candidates = session.get("movies", [])
        elif media_type == PLEX_TYPE_SHOW:
            candidates = session.get("shows", [])
        else:
            candidates = session.get("music", [])

        match = next(
            (item for item in candidates if chosen_title in item.get("title", "").lower()),
            None,
        )

        if not match:
            results = await self.coordinator.search(chosen_title, media_type=media_type)
            match = results[0] if results else None

        if not match:
            _clear_session(self.hass, conv_key)
            response = intent_obj.create_response()
            response.async_set_speech(f"I couldn't find {chosen_title} on Plex. Please try again.")
            return response

        return await _confirm_and_play(self.hass, self.coordinator, intent_obj, match, room, conv_key)


class PlexClarifyDeviceIntent(intent.IntentHandler):
    """Handle follow-up: user names a device after being asked 'which device?'."""

    intent_type = INTENT_CLARIFY_DEVICE
    slot_schema = {
        vol.Required("device"): intent.non_empty_string,
    }

    def __init__(self, hass: HomeAssistant, coordinator: PlexVoiceCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        conv_key = _conversation_key(intent_obj)
        device_hint: str = intent_obj.slots.get("device", {}).get("value", "")

        session = _get_session(self.hass, conv_key)
        pending_item = session.get("pending_item")

        if not pending_item:
            response = intent_obj.create_response()
            response.async_set_speech(
                "I'm not sure what you'd like to play. Try saying 'play something on Plex' first."
            )
            return response

        player_entity_id = _find_player_entity(self.hass, device_hint)
        if not player_entity_id:
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I couldn't find a Plex player called {device_hint}. "
                f"Please check the device name and try again."
            )
            return response

        return await _confirm_and_play(
            self.hass, self.coordinator, intent_obj, pending_item, device_hint, conv_key
        )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _confirm_and_play(
    hass: HomeAssistant,
    coordinator: PlexVoiceCoordinator,
    intent_obj: intent.Intent,
    item: dict,
    room: str,
    conv_key: str,
) -> intent.IntentResponse:
    """Resolve the target player, send the play command, and speak confirmation."""
    title = item.get("title", "that")
    media_key = item.get("ratingKey", "")
    media_type = item.get("type", PLEX_TYPE_MOVIE)

    player_entity_id: str | None = None
    player_name = "your device"

    if room:
        player_entity_id = _find_player_entity(hass, room)
        if player_entity_id:
            state = hass.states.get(player_entity_id)
            player_name = state.attributes.get("friendly_name", room) if state else room
        else:
            _clear_session(hass, conv_key)
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {title} but couldn't find a Plex player in the {room}. "
                f"Check that the device is on and Plex is open."
            )
            return response

    if not player_entity_id:
        plex_players = get_plex_player_entities(hass)
        if not plex_players:
            _clear_session(hass, conv_key)
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {title} but there are no Plex players configured. "
                f"Please check the integration setup."
            )
            return response
        if len(plex_players) == 1:
            player_entity_id, player_name = plex_players[0]
        else:
            names = [name for _, name in plex_players]
            # Keep session alive so PlexClarifyDeviceIntent can use it.
            session["pending_item"] = item
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {title}. Which device? You have: {_format_list(names)}."
            )
            return response

    machine_id = get_machine_id_for_entity(hass, player_entity_id)
    if machine_id:
        await coordinator.play_on_client(machine_id, media_key, media_type)
    else:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {"entity_id": player_entity_id, "media_content_id": media_key, "media_content_type": media_type},
            blocking=False,
        )

    _clear_session(hass, conv_key)
    response = intent_obj.create_response()
    response.async_set_speech(f"Okay! Playing {title} on {player_name}.")
    return response


def _format_list(items: list[str]) -> str:
    """Format a list for natural speech: 'a, b, and c'."""
    if not items:
        return "nothing"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"
