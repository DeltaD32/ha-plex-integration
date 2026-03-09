"""Media player platform for Plex Voice."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, PLEX_TYPE_MOVIE, PLEX_TYPE_SHOW
from .coordinator import PlexVoiceCoordinator

_LOGGER = logging.getLogger(__name__)

SUPPORT_PLEX_VOICE = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.SELECT_SOURCE
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Plex Voice media player entities."""
    coordinator: PlexVoiceCoordinator = hass.data[DOMAIN][entry.entry_id]
    clients = await coordinator.async_refresh_clients()

    entities = [PlexVoiceMediaPlayer(coordinator, client) for client in clients]

    # Always add a "virtual" player representing the server itself for browsing
    entities.append(PlexVoiceServerBrowser(coordinator))

    async_add_entities(entities, update_before_add=True)


class PlexVoiceServerBrowser(MediaPlayerEntity):
    """A virtual media player that represents the Plex server for browsing."""

    _attr_has_entity_name = True
    _attr_name = "Plex Library Browser"
    _attr_supported_features = MediaPlayerEntityFeature.BROWSE_MEDIA

    def __init__(self, coordinator: PlexVoiceCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_browser_{coordinator.server_name}"
        self._attr_state = MediaPlayerState.IDLE

    async def async_browse_media(self, media_content_type=None, media_content_id=None) -> BrowseMedia:
        return await _build_browse_tree(self._coordinator, media_content_type, media_content_id)


class PlexVoiceMediaPlayer(MediaPlayerEntity):
    """Represents a Plex client device as a media player."""

    _attr_has_entity_name = True
    _attr_supported_features = SUPPORT_PLEX_VOICE

    def __init__(self, coordinator: PlexVoiceCoordinator, client: dict) -> None:
        self._coordinator = coordinator
        self._client = client
        self._machine_id = client.get("machineIdentifier", "")
        client_name = client.get("name", "Unknown")
        self._attr_name = f"Plex - {client_name}"
        self._attr_unique_id = f"{DOMAIN}_{self._machine_id}"
        self._attr_state = MediaPlayerState.IDLE
        self._attr_source = client_name
        self._current_media: dict | None = None

    @property
    def media_title(self) -> str | None:
        if self._current_media:
            return self._current_media.get("title")
        return None

    @property
    def media_image_url(self) -> str | None:
        if self._current_media:
            thumb = self._current_media.get("thumb")
            return self._coordinator.get_thumbnail_url(thumb)
        return None

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Play a media item on this Plex client."""
        item = await self._coordinator.get_item_by_key(media_id)
        if not item:
            _LOGGER.error("Plex Voice: could not find media with key %s", media_id)
            return

        plex_type = item.get("type", PLEX_TYPE_MOVIE)
        success = await self._coordinator.play_on_client(self._machine_id, media_id, plex_type)
        if success:
            self._current_media = item
            self._attr_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()
        else:
            _LOGGER.error("Plex Voice: failed to play %s on %s", media_id, self._machine_id)

    async def async_browse_media(self, media_content_type=None, media_content_id=None) -> BrowseMedia:
        return await _build_browse_tree(self._coordinator, media_content_type, media_content_id)


async def _build_browse_tree(
    coordinator: PlexVoiceCoordinator,
    media_content_type: str | None,
    media_content_id: str | None,
) -> BrowseMedia:
    """Build a BrowseMedia tree from Plex library data."""

    # Root level: show all libraries
    if not media_content_id or media_content_id == "root":
        children = []
        for lib in coordinator.libraries:
            lib_type = lib.get("type", "unknown")
            children.append(
                BrowseMedia(
                    title=lib.get("title", "Library"),
                    media_class=_plex_type_to_media_class(lib_type),
                    media_content_id=f"library:{lib['key']}:{lib_type}",
                    media_content_type=lib_type,
                    can_play=False,
                    can_expand=True,
                    thumbnail=None,
                )
            )
        return BrowseMedia(
            title=coordinator.server_name,
            media_class="directory",
            media_content_id="root",
            media_content_type="root",
            can_play=False,
            can_expand=True,
            children=children,
        )

    # Library level: show items in a section
    if media_content_id.startswith("library:"):
        _, section_id, lib_type = media_content_id.split(":", 2)
        items = await coordinator.get_library_items(section_id)
        children = []
        for item in items:
            item_type = item.get("type", lib_type)
            thumb = coordinator.get_thumbnail_url(item.get("thumb"))
            children.append(
                BrowseMedia(
                    title=item.get("title", "Unknown"),
                    media_class=_plex_type_to_media_class(item_type),
                    media_content_id=item.get("ratingKey", ""),
                    media_content_type=item_type,
                    can_play=(item_type == PLEX_TYPE_MOVIE),
                    can_expand=(item_type == PLEX_TYPE_SHOW),
                    thumbnail=thumb,
                )
            )
        return BrowseMedia(
            title=f"Library: {section_id}",
            media_class="directory",
            media_content_id=media_content_id,
            media_content_type="library",
            can_play=False,
            can_expand=True,
            children=children,
        )

    # Fallback: single item (show, movie)
    item = await coordinator.get_item_by_key(media_content_id)
    if not item:
        raise ValueError(f"Unknown media_content_id: {media_content_id}")

    item_type = item.get("type", "unknown")
    thumb = coordinator.get_thumbnail_url(item.get("thumb"))
    return BrowseMedia(
        title=item.get("title", "Unknown"),
        media_class=_plex_type_to_media_class(item_type),
        media_content_id=media_content_id,
        media_content_type=item_type,
        can_play=True,
        can_expand=False,
        thumbnail=thumb,
    )


def _plex_type_to_media_class(plex_type: str) -> str:
    mapping = {
        "movie": "movie",
        "show": "tv_show",
        "season": "season",
        "episode": "episode",
        "artist": "artist",
        "album": "album",
        "track": "track",
    }
    return mapping.get(plex_type, "directory")
