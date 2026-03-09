"""Coordinator for Plex Voice integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_PLEX_URL, CONF_PLEX_TOKEN, CONF_SERVER_NAME

_LOGGER = logging.getLogger(__name__)

PLEX_HEADERS = {"Accept": "application/json"}


class PlexVoiceCoordinator:
    """Manages connection to Plex and caches library data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.plex_url: str = entry.data[CONF_PLEX_URL].rstrip("/")
        self.plex_token: str = entry.data[CONF_PLEX_TOKEN]
        self.server_name: str = entry.data.get(CONF_SERVER_NAME, "Plex")
        self._session: aiohttp.ClientSession | None = None
        self._libraries: list[dict] = []
        self._clients: list[dict] = []

    def _headers(self) -> dict:
        return {
            **PLEX_HEADERS,
            "X-Plex-Token": self.plex_token,
        }

    def _url(self, path: str, **params) -> str:
        param_str = "&".join(f"{k}={v}" for k, v in params.items())
        base = f"{self.plex_url}{path}?X-Plex-Token={self.plex_token}"
        if param_str:
            base += f"&{param_str}"
        return base

    async def async_setup(self) -> None:
        """Connect and load initial data."""
        self._session = async_get_clientsession(self.hass)
        await self._fetch_libraries()
        await self._fetch_clients()
        _LOGGER.info("Plex Voice: connected to %s, %d libraries found", self.server_name, len(self._libraries))

    async def _fetch_libraries(self) -> None:
        """Fetch all library sections."""
        url = self._url("/library/sections")
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()
        self._libraries = data.get("MediaContainer", {}).get("Directory", [])

    async def _fetch_clients(self) -> None:
        """Fetch available Plex clients/players."""
        url = self._url("/clients")
        try:
            async with self._session.get(url, headers=PLEX_HEADERS) as resp:
                resp.raise_for_status()
                data = await resp.json()
            self._clients = data.get("MediaContainer", {}).get("Server", [])
        except Exception:
            self._clients = []

    async def async_refresh_clients(self) -> list[dict]:
        """Refresh and return active Plex clients."""
        await self._fetch_clients()
        return self._clients

    @property
    def libraries(self) -> list[dict]:
        return self._libraries

    @property
    def clients(self) -> list[dict]:
        return self._clients

    async def search(self, query: str, media_type: str | None = None) -> list[dict]:
        """Search across all libraries for a title."""
        url = self._url("/search", query=query, limit=20)
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()

        results = []
        container = data.get("MediaContainer", {})

        # Results can be in different keys depending on type
        for key in ("Metadata", "Video", "Directory"):
            items = container.get(key, [])
            for item in items:
                item_type = item.get("type", "")
                if media_type and item_type != media_type:
                    continue
                results.append(item)

        return results

    async def get_library_items(self, section_id: str, media_type: str | None = None) -> list[dict]:
        """Get all items in a library section."""
        url = self._url(f"/library/sections/{section_id}/all")
        async with self._session.get(url, headers=PLEX_HEADERS) as resp:
            resp.raise_for_status()
            data = await resp.json()
        items = data.get("MediaContainer", {}).get("Metadata", [])
        if media_type:
            items = [i for i in items if i.get("type") == media_type]
        return items

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

    async def play_on_client(self, client_machine_id: str, media_key: str, media_type: str) -> bool:
        """Tell a Plex client to play a specific item."""
        # Plex remote control: /player/playback/playMedia
        url = (
            f"{self.plex_url}/player/playback/playMedia"
            f"?X-Plex-Token={self.plex_token}"
            f"&machineIdentifier={client_machine_id}"
            f"&key={media_key}"
            f"&type={media_type}"
            f"&X-Plex-Target-Client-Identifier={client_machine_id}"
        )
        try:
            async with self._session.get(url, headers=PLEX_HEADERS) as resp:
                return resp.status in (200, 204)
        except Exception as err:
            _LOGGER.error("Failed to send play command: %s", err)
            return False

    def get_thumbnail_url(self, thumb_path: str) -> str | None:
        """Return full URL for a Plex thumbnail."""
        if not thumb_path:
            return None
        return f"{self.plex_url}{thumb_path}?X-Plex-Token={self.plex_token}"
