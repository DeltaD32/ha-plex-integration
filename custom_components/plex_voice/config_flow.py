"""Config flow for Plex Voice integration."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, CONF_PLEX_URL, CONF_PLEX_TOKEN, CONF_SERVER_NAME

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


class PlexVoiceConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Plex Voice."""

    VERSION = 1

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
                if err.status == 401:
                    errors["base"] = "invalid_auth"
                else:
                    errors["base"] = "cannot_connect"
            except Exception:
                _LOGGER.exception("Unexpected error validating Plex connection")
                errors["base"] = "cannot_connect"
            else:
                # Use machine identifier to avoid duplicate entries
                await self.async_set_unique_id(info["machine_identifier"])
                self._abort_if_unique_id_configured()

                server_name = user_input.get(CONF_SERVER_NAME) or info["friendly_name"]
                return self.async_create_entry(
                    title=server_name,
                    data={
                        CONF_PLEX_URL: user_input[CONF_PLEX_URL],
                        CONF_PLEX_TOKEN: user_input[CONF_PLEX_TOKEN],
                        CONF_SERVER_NAME: server_name,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
            description_placeholders={
                "token_url": "https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/"
            },
        )
