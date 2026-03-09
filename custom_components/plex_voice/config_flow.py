"""Config flow for Plex Voice integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er, selector

from .const import DOMAIN, CONF_PLEX_URL, CONF_PLEX_TOKEN, CONF_SERVER_NAME, CONF_MONITORED_CLIENTS

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PLEX_URL, description={"suggested_value": "http://192.168.1.x:32400"}): str,
        vol.Required(CONF_PLEX_TOKEN): str,
        vol.Optional(CONF_SERVER_NAME, default="Plex"): str,
    }
)


async def validate_plex_connection(hass, url: str, token: str) -> dict:
    """Test the Plex URL and token. Returns server info or raises."""
    session = async_get_clientsession(hass)
    full_url = f"{url.rstrip('/')}/?X-Plex-Token={token}"
    headers = {"Accept": "application/json"}
    async with session.get(full_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
        resp.raise_for_status()
        data = await resp.json()
    container = data.get("MediaContainer", {})
    return {
        "friendly_name": container.get("friendlyName", "Plex Server"),
        "version": container.get("version", ""),
        "machine_identifier": container.get("machineIdentifier", ""),
    }


def get_ha_media_player_clients(hass) -> list[dict]:
    """Return media_player entities from HA as selectable Plex clients.

    For entities from the official HA Plex integration their entity registry
    unique_id *is* the Plex machineIdentifier, so the coordinator can use it
    directly.  For other media_player entities the entity_id is stored as the
    id and session matching / playback commands will not work — but the entry
    will at least appear for voice targeting.

    Entities are always available (unlike /clients which only shows active
    players), so the picker is never empty.
    """
    ent_reg = er.async_get(hass)
    clients: list[dict] = []
    seen_ids: set[str] = set()

    for entry in ent_reg.entities.values():
        if entry.domain != "media_player":
            continue
        # Prefer the Plex machineIdentifier stored as unique_id by the
        # official HA Plex integration.  Fall back to entity_id.
        client_id = entry.unique_id if entry.platform == "plex" else entry.entity_id
        if not client_id or client_id in seen_ids:
            continue
        seen_ids.add(client_id)

        state = hass.states.get(entry.entity_id)
        name = (
            (state.attributes.get("friendly_name") if state else None)
            or entry.original_name
            or entry.entity_id
        )
        clients.append({"id": client_id, "name": name})

    return sorted(clients, key=lambda c: c["name"].lower())


def _devices_schema(available_clients: list[dict], current_ids: list[str]) -> vol.Schema:
    """Build the device multi-select schema."""
    options = [{"value": c["id"], "label": c["name"]} for c in available_clients]
    return vol.Schema(
        {
            vol.Optional(CONF_MONITORED_CLIENTS, default=current_ids): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


class PlexVoiceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Plex Voice."""

    VERSION = 1

    def __init__(self) -> None:
        self._plex_url: str = ""
        self._plex_token: str = ""
        self._server_name: str = "Plex"
        self._available_clients: list[dict] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> PlexVoiceOptionsFlow:
        return PlexVoiceOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_plex_connection(
                    self.hass,
                    user_input[CONF_PLEX_URL],
                    user_input[CONF_PLEX_TOKEN],
                )
            except aiohttp.ClientResponseError as err:
                errors["base"] = "invalid_auth" if err.status == 401 else "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Plex connection")
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(info["machine_identifier"])
                self._abort_if_unique_id_configured()

                self._plex_url = user_input[CONF_PLEX_URL]
                self._plex_token = user_input[CONF_PLEX_TOKEN]
                self._server_name = user_input.get(CONF_SERVER_NAME) or info["friendly_name"]
                self._available_clients = get_ha_media_player_clients(self.hass)
                return await self.async_step_devices()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_devices(self, user_input: dict[str, Any] | None = None):
        """Let the user pick which Plex clients to monitor."""
        if user_input is not None:
            selected_ids: list[str] = user_input.get(CONF_MONITORED_CLIENTS, [])
            monitored = [c for c in self._available_clients if c["id"] in selected_ids]
            return self.async_create_entry(
                title=self._server_name,
                data={
                    CONF_PLEX_URL: self._plex_url,
                    CONF_PLEX_TOKEN: self._plex_token,
                    CONF_SERVER_NAME: self._server_name,
                    CONF_MONITORED_CLIENTS: monitored,
                },
            )

        return self.async_show_form(
            step_id="devices",
            data_schema=_devices_schema(self._available_clients, []),
        )


class PlexVoiceOptionsFlow(config_entries.OptionsFlow):
    """Allow reconfiguring the integration after setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry
        self._plex_url: str = ""
        self._plex_token: str = ""
        self._server_name: str = "Plex"
        self._available_clients: list[dict] = []

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await validate_plex_connection(
                    self.hass,
                    user_input[CONF_PLEX_URL],
                    user_input[CONF_PLEX_TOKEN],
                )
            except aiohttp.ClientResponseError as err:
                errors["base"] = "invalid_auth" if err.status == 401 else "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Plex connection")
                errors["base"] = "cannot_connect"
            else:
                self._plex_url = user_input[CONF_PLEX_URL]
                self._plex_token = user_input[CONF_PLEX_TOKEN]
                self._server_name = user_input.get(CONF_SERVER_NAME, self.config_entry.data.get(CONF_SERVER_NAME, "Plex"))
                self._available_clients = get_ha_media_player_clients(self.hass)
                return await self.async_step_devices()

        current = self.config_entry.data
        schema = vol.Schema(
            {
                vol.Required(CONF_PLEX_URL, default=current.get(CONF_PLEX_URL, "")): str,
                vol.Required(CONF_PLEX_TOKEN, default=current.get(CONF_PLEX_TOKEN, "")): str,
                vol.Optional(CONF_SERVER_NAME, default=current.get(CONF_SERVER_NAME, "Plex")): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    async def async_step_devices(self, user_input: dict[str, Any] | None = None):
        """Let the user pick which Plex clients to monitor."""
        if user_input is not None:
            selected_ids: list[str] = user_input.get(CONF_MONITORED_CLIENTS, [])
            monitored = [c for c in self._available_clients if c["id"] in selected_ids]
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    CONF_PLEX_URL: self._plex_url,
                    CONF_PLEX_TOKEN: self._plex_token,
                    CONF_SERVER_NAME: self._server_name,
                    CONF_MONITORED_CLIENTS: monitored,
                },
            )
            return self.async_create_entry(title="", data={})

        # Pre-select currently monitored clients
        current_ids = [c["id"] for c in self.config_entry.data.get(CONF_MONITORED_CLIENTS, [])]
        return self.async_show_form(
            step_id="devices",
            data_schema=_devices_schema(self._available_clients, current_ids),
        )
