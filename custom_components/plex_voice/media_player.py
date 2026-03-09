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
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, PLEX_TYPE_EPISODE, PLEX_TYPE_MOVIE, PLEX_TYPE_SEASON, PLEX_TYPE_SHOW
from .coordinator import PlexVoiceCoordinator

_LOGGER = logging.getLogger(__name__)

SUPPORT_PLEX_VOICE = (
    MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.BROWSE_MEDIA
    | MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.SEEK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Plex Voice media player entities."""
    coordinator: PlexVoiceCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Build initial entities using the monitored-client list (or all known clients).
    initial_entities: list[MediaPlayerEntity] = [
        PlexVoiceMediaPlayer(coordinator, c)
        for c in coordinator.startup_client_list()
    ]
    # Always add the virtual server browser for the Media panel.
    initial_entities.append(PlexVoiceServerBrowser(coordinator))
    async_add_entities(initial_entities, update_before_add=True)

    # Register a callback so that clients discovered later (via active sessions)
    # automatically get an entity created without requiring a restart.
    def _on_new_client(client_info: dict) -> None:
        async_add_entities([PlexVoiceMediaPlayer(coordinator, client_info)], True)

    coordinator.register_new_client_callback(_on_new_client)


# ---------------------------------------------------------------------------
# Server browser entity (virtual, browse-only)
# ---------------------------------------------------------------------------


class PlexVoiceServerBrowser(MediaPlayerEntity):
    """A virtual media player representing the Plex server — used for browsing."""

    _attr_has_entity_name = True
    _attr_name = "Plex Library Browser"
    _attr_supported_features = MediaPlayerEntityFeature.BROWSE_MEDIA
    _attr_state = MediaPlayerState.IDLE

    def __init__(self, coordinator: PlexVoiceCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_browser_{coordinator.server_name}"

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return await _build_browse_tree(self._coordinator, media_content_type, media_content_id)


# ---------------------------------------------------------------------------
# Per-client media player entity
# ---------------------------------------------------------------------------


class PlexVoiceMediaPlayer(CoordinatorEntity[PlexVoiceCoordinator], MediaPlayerEntity):
    """Represents a Plex client as a fully-featured media player entity."""

    _attr_has_entity_name = True
    _attr_supported_features = SUPPORT_PLEX_VOICE

    def __init__(self, coordinator: PlexVoiceCoordinator, client: dict) -> None:
        super().__init__(coordinator)
        self._machine_id: str = client.get("machineIdentifier", "")
        client_name: str = client.get("name", self._machine_id)
        self._attr_name = f"Plex - {client_name}"
        self._attr_unique_id = f"{DOMAIN}_{self._machine_id}"

    # ------------------------------------------------------------------
    # State — derived from coordinator session data
    # ------------------------------------------------------------------

    def _session(self) -> dict:
        """Return current session data for this client (empty dict if idle)."""
        return self.coordinator.data.get(self._machine_id, {}) if self.coordinator.data else {}

    @property
    def state(self) -> MediaPlayerState:
        plex_state = self._session().get("state", "")
        if plex_state == "playing":
            return MediaPlayerState.PLAYING
        if plex_state == "paused":
            return MediaPlayerState.PAUSED
        if plex_state == "buffering":
            return MediaPlayerState.BUFFERING
        return MediaPlayerState.IDLE

    @property
    def media_title(self) -> str | None:
        return self._session().get("title") or None

    @property
    def media_series_title(self) -> str | None:
        return self._session().get("grandparentTitle") or None

    @property
    def media_season(self) -> int | None:
        val = self._session().get("parentIndex")
        return int(val) if val is not None else None

    @property
    def media_episode(self) -> int | None:
        val = self._session().get("index")
        return int(val) if val is not None else None

    @property
    def media_content_type(self) -> str | None:
        return self._session().get("type") or None

    @property
    def media_duration(self) -> float | None:
        ms = self._session().get("duration")
        return ms / 1000.0 if ms is not None else None

    @property
    def media_position(self) -> float | None:
        ms = self._session().get("viewOffset")
        return ms / 1000.0 if ms is not None else None

    @property
    def media_position_updated_at(self):
        if self._session().get("viewOffset") is not None:
            return self.coordinator.last_poll_time
        return None

    @property
    def media_image_url(self) -> str | None:
        thumb = self._session().get("thumb")
        return self.coordinator.get_thumbnail_url(thumb) if thumb else None

    # ------------------------------------------------------------------
    # Playback controls
    # ------------------------------------------------------------------

    async def async_media_play(self) -> None:
        await self.coordinator.playback_command(self._machine_id, "play")

    async def async_media_pause(self) -> None:
        await self.coordinator.playback_command(self._machine_id, "pause")

    async def async_media_stop(self) -> None:
        await self.coordinator.playback_command(self._machine_id, "stop")

    async def async_media_next_track(self) -> None:
        await self.coordinator.playback_command(self._machine_id, "skipNext")

    async def async_media_previous_track(self) -> None:
        await self.coordinator.playback_command(self._machine_id, "skipPrevious")

    async def async_media_seek(self, position: float) -> None:
        await self.coordinator.playback_seek(self._machine_id, int(position * 1000))

    async def async_set_volume_level(self, volume: float) -> None:
        await self.coordinator.playback_set_volume(self._machine_id, int(volume * 100))

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        """Play a media item on this Plex client."""
        item = await self.coordinator.get_item_by_key(media_id)
        if not item:
            _LOGGER.error("Plex Voice: could not find media with key %s", media_id)
            return
        plex_type = item.get("type", PLEX_TYPE_MOVIE)
        success = await self.coordinator.play_on_client(self._machine_id, media_id, plex_type)
        if not success:
            _LOGGER.error("Plex Voice: failed to play %s on %s", media_id, self._machine_id)

    # ------------------------------------------------------------------
    # Media browser
    # ------------------------------------------------------------------

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        return await _build_browse_tree(self.coordinator, media_content_type, media_content_id)


# ---------------------------------------------------------------------------
# Browse tree builder
# ---------------------------------------------------------------------------


async def _build_browse_tree(
    coordinator: PlexVoiceCoordinator,
    media_content_type: str | None,
    media_content_id: str | None,
) -> BrowseMedia:
    """Build a BrowseMedia tree from Plex library data."""

    # ---- Root: On Deck + Recently Added + all libraries ----
    if not media_content_id or media_content_id == "root":
        children = [
            BrowseMedia(
                title="On Deck",
                media_class="directory",
                media_content_id="on_deck",
                media_content_type="on_deck",
                can_play=False,
                can_expand=True,
                thumbnail=None,
            ),
            BrowseMedia(
                title="Recently Added",
                media_class="directory",
                media_content_id="recently_added",
                media_content_type="recently_added",
                can_play=False,
                can_expand=True,
                thumbnail=None,
            ),
        ]
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

    # ---- On Deck ----
    if media_content_id == "on_deck":
        items = await coordinator.get_on_deck()
        return BrowseMedia(
            title="On Deck",
            media_class="directory",
            media_content_id="on_deck",
            media_content_type="on_deck",
            can_play=False,
            can_expand=True,
            children=_items_to_browse_children(coordinator, items),
        )

    # ---- Recently Added ----
    if media_content_id == "recently_added":
        items = await coordinator.get_recently_added()
        return BrowseMedia(
            title="Recently Added",
            media_class="directory",
            media_content_id="recently_added",
            media_content_type="recently_added",
            can_play=False,
            can_expand=True,
            children=_items_to_browse_children(coordinator, items),
        )

    # ---- Library section ----
    if media_content_id.startswith("library:"):
        _, section_id, lib_type = media_content_id.split(":", 2)
        items = await coordinator.get_library_items(section_id)
        return BrowseMedia(
            title=f"Library",
            media_class="directory",
            media_content_id=media_content_id,
            media_content_type="library",
            can_play=False,
            can_expand=True,
            children=_items_to_browse_children(coordinator, items),
        )

    # ---- Children of a show or season (drill-down) ----
    if media_content_id.startswith("children:"):
        rating_key = media_content_id.split(":", 1)[1]
        parent = await coordinator.get_item_by_key(rating_key)
        children_items = await coordinator.get_children(rating_key)
        parent_title = parent.get("title", "Unknown") if parent else "Unknown"
        return BrowseMedia(
            title=parent_title,
            media_class=_plex_type_to_media_class(parent.get("type", "") if parent else ""),
            media_content_id=media_content_id,
            media_content_type=parent.get("type", "") if parent else "",
            can_play=False,
            can_expand=True,
            children=_items_to_browse_children(coordinator, children_items),
        )

    # ---- Single item fallback ----
    item = await coordinator.get_item_by_key(media_content_id)
    if not item:
        raise ValueError(f"Unknown media_content_id: {media_content_id}")

    item_type = item.get("type", "unknown")
    return BrowseMedia(
        title=item.get("title", "Unknown"),
        media_class=_plex_type_to_media_class(item_type),
        media_content_id=media_content_id,
        media_content_type=item_type,
        can_play=True,
        can_expand=False,
        thumbnail=coordinator.get_thumbnail_url(item.get("thumb")),
    )


def _items_to_browse_children(
    coordinator: PlexVoiceCoordinator, items: list[dict]
) -> list[BrowseMedia]:
    """Convert a list of Plex metadata items to BrowseMedia children."""
    children = []
    for item in items:
        item_type = item.get("type", "")
        rating_key = item.get("ratingKey", "")
        is_show_or_season = item_type in (PLEX_TYPE_SHOW, PLEX_TYPE_SEASON)
        children.append(
            BrowseMedia(
                title=item.get("title", "Unknown"),
                media_class=_plex_type_to_media_class(item_type),
                media_content_id=f"children:{rating_key}" if is_show_or_season else rating_key,
                media_content_type=item_type,
                can_play=not is_show_or_season,
                can_expand=is_show_or_season,
                thumbnail=coordinator.get_thumbnail_url(item.get("thumb")),
            )
        )
    return children


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
