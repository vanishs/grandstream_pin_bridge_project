"""UI setup and options flow; requires an explicit Web username and password.
"""
from __future__ import annotations

import ipaddress
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import webhook
from urllib.parse import urlsplit
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .api import AuthenticationError, DeviceError, GDSClient
from .const import (
    CONF_FORWARD_URL,
    CONF_FORWARD_TOKEN,
    CONF_NEW_FORWARD_TOKEN,
    CONF_MATCH_TIMEOUT,
    CONF_NEW_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_USE_HTTPS,
    CONF_VERIFY_SSL,
    CONF_WEBHOOK_ID,
    DEFAULT_HOST,
    DEFAULT_MATCH_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


def _build_client(hass, config: dict) -> GDSClient:
    """Build separate API client for credential testing."""
    scheme = "https" if config.get(CONF_USE_HTTPS, True) else "http"
    return GDSClient(
        async_get_clientsession(hass),
        f"{scheme}://{config[CONF_HOST]}:{config[CONF_PORT]}",
        config[CONF_USERNAME], config[CONF_PASSWORD],
        bool(config.get(CONF_VERIFY_SSL, False)),
    )


def _validate_host(host: str) -> bool:
    # Require an IP address so we can check HTTP callback source reliably.
    try:
        return isinstance(ipaddress.ip_address(host), ipaddress.IPv4Address) and not ipaddress.ip_address(host).is_unspecified
    except ValueError:
        return False


class GDS3725PINConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure device details with actual built-in Web user/admin account."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            if not _validate_host(user_input[CONF_HOST]):
                errors["base"] = "invalid_host"
            else:
                unique_id = f"{user_input[CONF_HOST]}:{user_input[CONF_PORT]}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                try:
                    await _build_client(self.hass, user_input).records(page_size=1)
                except AuthenticationError:
                    errors["base"] = "invalid_auth"
                except (DeviceError, OSError, ValueError):
                    errors["base"] = "cannot_connect"
                else:
                    config = dict(user_input)
                    config[CONF_WEBHOOK_ID] = webhook.async_generate_id()
                    return self.async_create_entry(
                        title=f"GDS3725 {user_input[CONF_HOST]}", data=config
                    )
        schema = vol.Schema({
            vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_USE_HTTPS, default=True): bool,
            vol.Required(CONF_VERIFY_SSL, default=False): bool,
            vol.Required(CONF_USERNAME, default=DEFAULT_USERNAME): str,
            vol.Required(CONF_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return GDS3725PINOptionsFlow(config_entry)


class GDS3725PINOptionsFlow(config_entries.OptionsFlow):
    """Expose URL for device P15529, and allow future credential changes."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(self, user_input: dict | None = None):
        current = {**self._entry.data, **self._entry.options}
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**current, **user_input}
            forward_url = str(user_input.get(CONF_FORWARD_URL, "") or "").strip()
            if forward_url and urlsplit(forward_url).scheme not in ("http", "https"):
                errors["base"] = "invalid_forward_url"
            candidate = user_input.get(CONF_NEW_PASSWORD, "").strip()
            merged[CONF_PASSWORD] = candidate or current[CONF_PASSWORD]
            # Credential edits are verified before being persisted.
            try:
                if errors:
                    raise ValueError("Invalid forwarding URL")
                await _build_client(self.hass, merged).records(page_size=1)
            except AuthenticationError:
                errors["base"] = "invalid_auth"
            except (DeviceError, OSError, ValueError):
                errors.setdefault("base", "cannot_connect")
            else:
                saved = dict(user_input)
                saved.pop(CONF_NEW_PASSWORD, None)
                token = saved.pop(CONF_NEW_FORWARD_TOKEN, "").strip()
                if token:
                    saved[CONF_FORWARD_TOKEN] = token
                elif current.get(CONF_FORWARD_TOKEN):
                    saved[CONF_FORWARD_TOKEN] = current[CONF_FORWARD_TOKEN]
                if candidate:
                    saved[CONF_PASSWORD] = candidate
                return self.async_create_entry(title="", data=saved)
        # URL is a secret bearer capability. The password is not embedded.
        webhook_path = f"/api/webhook/{self._entry.data[CONF_WEBHOOK_ID]}"
        schema = vol.Schema({
            vol.Required(CONF_USERNAME, default=current[CONF_USERNAME]): str,
            vol.Optional(CONF_FORWARD_URL, default=current.get(CONF_FORWARD_URL, "")): str,
            vol.Optional(CONF_NEW_FORWARD_TOKEN): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(CONF_NEW_PASSWORD): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_VERIFY_SSL, default=current.get(CONF_VERIFY_SSL, False)): bool,
            vol.Required(CONF_MATCH_TIMEOUT, default=current.get(CONF_MATCH_TIMEOUT, DEFAULT_MATCH_TIMEOUT)):
                vol.All(vol.Coerce(float), vol.Range(min=2.0, max=30.0)),
            vol.Required(CONF_POLL_INTERVAL, default=current.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)):
                vol.All(vol.Coerce(float), vol.Range(min=0.1, max=2.0)),
        })
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors,
            description_placeholders={"webhook_path": webhook_path},
        )
