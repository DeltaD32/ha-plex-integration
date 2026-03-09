"""Coordinator for Plex Voice integration."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_PLEX_URL, CONF_PLEX_TOKEN, CONF_SERVER_NAME, DOMAIN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)

PLEX_HEADERS = {"Accept": "application/json"}


class PlexVoiceCoordinator(DataUpdateCoordinator[dict[str, dict]]):
    """Manages connection to Plex, polls sessions, and caches library data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.entry = entry
        self.plex_url: str = entry.data[CONF_PLEX_URL].rstrip("/")
        self.plex_token: str = entry.data[CONF_PLEX_TOKEN]
        self.server_name: str = entry.data.get(CONF_SERVER_NAME, "Plex")
        self._session: aiohttp.ClientSession | None = None
        self._libraries: list[dict] = []
        self._known_machine_ids: set[str] = set()
        self._new_client_callbacks: list[Callable[[dict], None]] = []
        self.last_poll_time: Any = None

    def _url(self, path: str, **params) -> str:
        query = urlencode({"X-Plex-Token": self.plex_token, **params})
        return f"{self.plex_url}{path}?{query}"

    def register_new_client_callback(self, callback: Callable[[dict], None]) -> None:
        """Register a callback invoked when a previously-unseen Plex client appears."""
        self._new_client_callbacks.append(callback)

    async def async_setup(self) -> None:
        """Connect and load initial static data (libraries + initial client list)."""
        self._session = async_get_clientsession(self.hass)
        await self._fetch_libraries()
        # Pre-populate known machine IDs from the clients endpoint so we don't
        # fire new-entity callbacks for clients that were already known at startup.
        await self._prefetch_clients()
        _LOGGER.info(
            "Plex Voice: connected to %s, %d libraries found",
            self.server_name,
            len(self._libraries),
        )

    async def _fetch_libraries(self) -> None:
        """Fetch all library sections."""
        url = self._url("/library/sections")
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()
        self._libraries = data.get("MediaContainer", {}).get("Directory", [])

    async def _prefetch_clients(self) -> None:
        """Populate _known_machine_ids from /clients so startup entities are not re-created."""
        url = self._url("/clients")
        try:
            async with self._session.get(url, headers=PLEX_HEADERS) as resp:
                resp.raise_for_status()
                data = await resp.json()
            clients = data.get("MediaContainer", {}).get("Server", [])
            for client in clients:
                mid = client.get("machineIdentifier")
                if mid:
                    self._known_machine_ids.add(mid)
        except Exception:
            pass

    async def _async_update_data(self) -> dict[str, dict]:
        """Poll /status/sessions. Returns {machine_id: session_info}."""
        url = self._url("/status/sessions")
        try:
            async with self._session.get(url, headers=PLEX_HEADERS) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Plex: {err}") from err

        self.last_poll_time = dt_util.utcnow()

        sessions: dict[str, dict] = {}
        container = data.get("MediaContainer", {})
        new_clients: list[dict] = []

        for item in container.get("Video", []) + container.get("Track", []):
            player = item.get("Player", {})
            machine_id = player.get("machineIdentifier")
            if not machine_id:
                continue

            sessions[machine_id] = {
                "state": player.get("state", "idle"),
                "title": item.get("title", ""),
                "type": item.get("type", ""),
                "thumb": item.get("thumb", ""),
                "grandparentTitle": item.get("grandparentTitle", ""),
                "parentIndex": item.get("parentIndex"),
                "index": item.get("index"),
                "duration": item.get("duration"),
                "viewOffset": item.get("viewOffset"),
                "ratingKey": item.get("ratingKey", ""),
            }

            if machine_id not in self._known_machine_ids:
                self._known_machine_ids.add(machine_id)
                new_clients.append(
                    {
                        "machineIdentifier": machine_id,
                        "name": player.get("title") or player.get("product", machine_id),
                        "platform": player.get("platform", ""),
                    }
                )

        for client_info in new_clients:
            for cb in self._new_client_callbacks:
                cb(client_info)

        return sessions

    # ------------------------------------------------------------------
    # Library / metadata helpers
    # ------------------------------------------------------------------

    @property
    def libraries(self) -> list[dict]:
        return self._libraries

    async def search(self, query: str, media_type: str | None = None) -> list[dict]:
        """Search across all libraries for a title."""
        url = self._url("/search", query=query, limit=20)
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()

        results = []
        container = data.get("MediaContainer", {})
        for key in ("Metadata", "Video", "Directory"):
            for item in container.get(key, []):
                if media_type and item.get("type") != media_type:
                    continue
                results.append(item)
        return results

    async def get_library_items(self, section_id: str) -> list[dict]:
        """Get all items in a library section."""
        url = self._url(f"/library/sections/{section_id}/all")
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return data.get("MediaContainer", {}).get("Metadata", [])

    async def get_children(self, rating_key: str) -> list[dict]:
        """Get children of a container (seasons of a show, episodes of a season)."""
        url = self._url(f"/library/metadata/{rating_key}/children")
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        return data.get("MediaContainer", {}).get("Metadata", [])

    async def get_on_deck(self) -> list[dict]:
        """Return On Deck items (in-progress media)."""
        url = self._url("/library/onDeck")
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        return data.get("MediaContainer", {}).get("Metadata", [])

    async def get_recently_added(self) -> list[dict]:
        """Return recently added items."""
        url = self._url("/library/recentlyAdded")
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            if resp.status != 200:
                return []
            data = await resp.json()
        return data.get("MediaContainer", {}).get("Metadata", [])

    async def get_item_by_key(self, key: str) -> dict | None:
        """Fetch a single media item by its Plex key."""
        if not key.startswith("/"):
            key = f"/library/metadata/{key}"
        url = self._url(key)
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
        items = data.get("MediaContainer", {}).get("Metadata", [])
        return items[0] if items else None

    def get_thumbnail_url(self, thumb_path: str) -> str | None:
        """Return full URL for a Plex thumbnail."""
        if not thumb_path:
            return None
        return self._url(thumb_path)

    # ------------------------------------------------------------------
    # Playback control helpers
    # ------------------------------------------------------------------

    async def play_on_client(self, machine_id: str, media_key: str, media_type: str) -> bool:
        """Tell a Plex client to play a specific item."""
        url = self._url(
            "/player/playback/playMedia",
            machineIdentifier=machine_id,
            key=media_key,
            type=media_type,
            **{"X-Plex-Target-Client-Identifier": machine_id},
        )
        try:
            async with self._session.get(url, headers=PLEX_HEADERS) as resp:
                return resp.status in (200, 204)
        except Exception as err:
            _LOGGER.error("Failed to send play command: %s", err)
            return False

    async def playback_command(self, machine_id: str, command: str) -> bool:
        """Send a simple playback command (pause, play, stop, skipNext, skipPrevious)."""
        url = self._url(
            f"/player/playback/{command}",
            **{"X-Plex-Target-Client-Identifier": machine_id},
        )
        try:
            async with self._session.get(url, headers=PLEX_HEADERS) as resp:
                return resp.status in (200, 204)
        except Exception as err:
            _LOGGER.error("Playback command '%s' failed: %s", command, err)
            return False

    async def playback_seek(self, machine_id: str, offset_ms: int) -> bool:
        """Seek to position (offset in milliseconds)."""
        url = self._url(
            "/player/playback/seekTo",
            offset=offset_ms,
            **{"X-Plex-Target-Client-Identifier": machine_id},
        )
        try:
            async with self._session.get(url, headers=PLEX_HEADERS) as resp:
                return resp.status in (200, 204)
        except Exception as err:
            _LOGGER.error("Seek failed: %s", err)
            return False

    async def playback_set_volume(self, machine_id: str, volume_pct: int) -> bool:
        """Set volume (0-100)."""
        url = self._url(
            "/player/playback/setParameters",
            volume=volume_pct,
            **{"X-Plex-Target-Client-Identifier": machine_id},
        )
        try:
            async with self._session.get(url, headers=PLEX_HEADERS) as resp:
                return resp.status in (200, 204)
        except Exception as err:
            _LOGGER.error("Volume set failed: %s", err)
            return False
