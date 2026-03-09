"""Intent handlers for Plex Voice assistant integration.

This is the core of the conversational voice flow:
  "Play Star Wars on the living room TV"
  → search Plex
  → if ambiguous: ask movie or show?
  → if still ambiguous: list matches, ask which one
  → confirm and play
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import intent

from .const import (
    DOMAIN,
    INTENT_PLAY_MEDIA,
    INTENT_CLARIFY_TYPE,
    INTENT_CLARIFY_TITLE,
    PLEX_TYPE_MOVIE,
    PLEX_TYPE_SHOW,
    SESSION_KEY,
)
from .coordinator import PlexVoiceCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_intents(hass: HomeAssistant, coordinator: PlexVoiceCoordinator) -> None:
    """Register all Plex Voice intent handlers."""
    intent.async_register(hass, PlexPlayMediaIntent(hass, coordinator))
    intent.async_register(hass, PlexClarifyTypeIntent(hass, coordinator))
    intent.async_register(hass, PlexClarifyTitleIntent(hass, coordinator))
    _LOGGER.info("Plex Voice: registered voice intent handlers")


def _get_session(hass: HomeAssistant) -> dict:
    """Get or create the conversation session store."""
    if SESSION_KEY not in hass.data:
        hass.data[SESSION_KEY] = {}
    return hass.data[SESSION_KEY]


def _clear_session(hass: HomeAssistant) -> None:
    hass.data[SESSION_KEY] = {}


def _find_player_entity(hass: HomeAssistant, location_hint: str) -> str | None:
    """Try to match a spoken location to a media_player entity."""
    hint = location_hint.lower().strip()
    for entity_id in hass.states.async_entity_ids("media_player"):
        state = hass.states.get(entity_id)
        if not state:
            continue
        name = state.attributes.get("friendly_name", "").lower()
        if hint in name or hint in entity_id.lower():
            return entity_id
    return None


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
        title = slots.get("title", {}).get("value", "")
        room = slots.get("room", {}).get("value", "")

        if not title:
            response = intent_obj.create_response()
            response.async_set_speech("What would you like to play from Plex?")
            return response

        # Search Plex
        results = await self.coordinator.search(title)

        if not results:
            response = intent_obj.create_response()
            response.async_set_speech(f"I couldn't find anything called {title} on your Plex server.")
            return response

        movies = [r for r in results if r.get("type") == PLEX_TYPE_MOVIE]
        shows = [r for r in results if r.get("type") == PLEX_TYPE_SHOW]

        session = _get_session(self.hass)
        session["title_query"] = title
        session["room"] = room
        session["movies"] = movies
        session["shows"] = shows

        # Case 1: only movies found
        if movies and not shows:
            if len(movies) == 1:
                return await _confirm_and_play(self.hass, self.coordinator, intent_obj, movies[0], room)
            session["media_type"] = PLEX_TYPE_MOVIE
            titles = _format_list([m.get("title", "?") for m in movies])
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {len(movies)} movies matching {title}: {titles}. Which one would you like?"
            )
            return response

        # Case 2: only shows found
        if shows and not movies:
            if len(shows) == 1:
                return await _confirm_and_play(self.hass, self.coordinator, intent_obj, shows[0], room)
            session["media_type"] = PLEX_TYPE_SHOW
            titles = _format_list([s.get("title", "?") for s in shows])
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {len(shows)} shows matching {title}: {titles}. Which one would you like?"
            )
            return response

        # Case 3: both movies AND shows — ask for clarification
        response = intent_obj.create_response()
        response.async_set_speech(
            f"I found {title} as both a movie and a show on Plex. "
            f"Would you like the movie or the show?"
        )
        return response


class PlexClarifyTypeIntent(intent.IntentHandler):
    """Handle follow-up: 'movie' or 'show' after ambiguous search."""

    intent_type = INTENT_CLARIFY_TYPE
    slot_schema = {
        "media_type": intent.non_empty_string,
    }

    def __init__(self, hass: HomeAssistant, coordinator: PlexVoiceCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = intent_obj.slots
        media_type_str = slots.get("media_type", {}).get("value", "").lower()

        session = _get_session(self.hass)
        if not session.get("title_query"):
            response = intent_obj.create_response()
            response.async_set_speech("I'm not sure what you're referring to. Try saying 'play something on Plex' first.")
            return response

        media_type = PLEX_TYPE_MOVIE if "movie" in media_type_str else PLEX_TYPE_SHOW
        session["media_type"] = media_type
        results = session.get("movies" if media_type == PLEX_TYPE_MOVIE else "shows", [])
        room = session.get("room", "")

        if not results:
            response = intent_obj.create_response()
            response.async_set_speech(f"I couldn't find any {media_type_str} matching that title.")
            _clear_session(self.hass)
            return response

        if len(results) == 1:
            return await _confirm_and_play(self.hass, self.coordinator, intent_obj, results[0], room)

        # Multiple options — list them
        titles = _format_list([r.get("title", "?") for r in results])
        response = intent_obj.create_response()
        response.async_set_speech(
            f"I found these {media_type_str}s: {titles}. Which one would you like?"
        )
        return response


class PlexClarifyTitleIntent(intent.IntentHandler):
    """Handle follow-up: user picks a specific title from a list."""

    intent_type = INTENT_CLARIFY_TITLE
    slot_schema = {
        "title": intent.non_empty_string,
    }

    def __init__(self, hass: HomeAssistant, coordinator: PlexVoiceCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator

    async def async_handle(self, intent_obj: intent.Intent) -> intent.IntentResponse:
        slots = intent_obj.slots
        chosen_title = slots.get("title", {}).get("value", "").lower()

        session = _get_session(self.hass)
        media_type = session.get("media_type", PLEX_TYPE_MOVIE)
        room = session.get("room", "")
        candidates = session.get("movies" if media_type == PLEX_TYPE_MOVIE else "shows", [])

        # Fuzzy match by title
        match = None
        for item in candidates:
            if chosen_title in item.get("title", "").lower():
                match = item
                break

        if not match:
            # Re-search with the clarified title
            results = await self.coordinator.search(chosen_title, media_type=media_type)
            if results:
                match = results[0]

        if not match:
            response = intent_obj.create_response()
            response.async_set_speech(f"I couldn't find {chosen_title} on Plex. Please try again.")
            _clear_session(self.hass)
            return response

        return await _confirm_and_play(self.hass, self.coordinator, intent_obj, match, room)


async def _confirm_and_play(
    hass: HomeAssistant,
    coordinator: PlexVoiceCoordinator,
    intent_obj: intent.Intent,
    item: dict,
    room: str,
) -> intent.IntentResponse:
    """Find the right player, send play command, speak confirmation."""
    title = item.get("title", "that")
    media_key = item.get("ratingKey", "")
    media_type = item.get("type", PLEX_TYPE_MOVIE)

    # Resolve player entity
    player_entity_id = None
    player_name = "your device"

    if room:
        player_entity_id = _find_player_entity(hass, room)
        if player_entity_id:
            state = hass.states.get(player_entity_id)
            player_name = state.attributes.get("friendly_name", room) if state else room
        else:
            # Room given but no player found
            _clear_session(hass)
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {title} but I couldn't find a Plex player in the {room}. "
                f"Check that the device is on and Plex is open."
            )
            return response

    # If no room given, see if there's only one active player
    if not player_entity_id:
        plex_players = [
            eid for eid in hass.states.async_entity_ids("media_player")
            if eid.startswith("media_player.plex_")
        ]
        if not plex_players:
            _clear_session(hass)
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {title} but there are no active Plex players. "
                f"Please open Plex on a device first."
            )
            return response
        if len(plex_players) == 1:
            player_entity_id = plex_players[0]
            state = hass.states.get(player_entity_id)
            player_name = state.attributes.get("friendly_name", player_entity_id) if state else player_entity_id
        else:
            # Ask which player
            names = [
                hass.states.get(e).attributes.get("friendly_name", e)
                for e in plex_players if hass.states.get(e)
            ]
            _clear_session(hass)
            response = intent_obj.create_response()
            response.async_set_speech(
                f"I found {title}. Which device would you like to play it on? "
                f"You have: {_format_list(names)}."
            )
            return response

    # Send play command via service call
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": player_entity_id,
            "media_content_id": media_key,
            "media_content_type": media_type,
        },
        blocking=False,
    )

    _clear_session(hass)
    response = intent_obj.create_response()
    response.async_set_speech(f"Okay! Playing {title} on {player_name}.")
    return response


def _format_list(items: list[str]) -> str:
    """Format a list for speech: 'a, b, and c'."""
    if not items:
        return "nothing"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"
