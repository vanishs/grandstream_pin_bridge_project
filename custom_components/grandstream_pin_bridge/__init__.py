"""Grandstream GDS3725 Private PIN → HA webhook, event and sensor integration.

Original Grandstream Home repo inspired the custom_components framework only;
unused remote unlock, SIP, camera and NAS features are intentionally removed.
"""
from __future__ import annotations

import ipaddress
import logging
from urllib.parse import urlsplit

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthenticationError, DeviceError, GDSClient
from .bridge import PinBridge
from .device_panel import async_register_device_panel
from .const import CONF_USE_HTTPS, CONF_VERIFY_SSL, CONF_WEBHOOK_ID, DOMAIN

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR]


def create_client(hass: HomeAssistant, config: dict) -> GDSClient:
    """Shared device client construction for setup and future GDS platforms."""
    scheme = "https" if config.get(CONF_USE_HTTPS, True) else "http"
    host = config[CONF_HOST]
    port = config[CONF_PORT]
    base = f"{scheme}://{host}:{port}"
    return GDSClient(
        async_get_clientsession(hass), base,
        str(config[CONF_USERNAME]), str(config[CONF_PASSWORD]),
        bool(config.get(CONF_VERIFY_SSL, False))
    )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register shared frontend resources once before loading device entries."""
    await async_register_device_panel(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    cfg = {**entry.data, **entry.options}
    client = create_client(hass, cfg)
    bridge = PinBridge(hass, entry.entry_id, client, cfg)
    try:
        await bridge.start()
    except (DeviceError, ValueError) as exc:
        raise ConfigEntryNotReady("GDS device login/door_record unavailable") from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = bridge
    host = cfg[CONF_HOST]
    expected_ip = None
    try:
        expected_ip = ipaddress.ip_address(host)
    except ValueError:
        _LOGGER.warning("GDS host is not a numeric IP; source IP filter unavailable")

    async def handle_pin_webhook(
        _hass: HomeAssistant, _webhook_id: str, request: web.Request
    ) -> web.Response:
        # A random, unguessable HA webhook URL is the primary bearer secret;
        # local-only and an additional device IP check are layered defenses.
        if expected_ip is not None:
            try:
                if ipaddress.ip_address(request.remote or "") != expected_ip:
                    _LOGGER.warning("Rejected PIN callback from unexpected source IP")
                    return web.Response(status=403, text="Forbidden")
            except ValueError:
                return web.Response(status=403, text="Forbidden")
        if bridge.enqueue():
            return web.Response(status=200, text="OK")
        return web.Response(status=503, text="Busy")

    try:
        webhook.async_register(
            hass, DOMAIN, "GDS3725 Private PIN", entry.data[CONF_WEBHOOK_ID],
            handle_pin_webhook, local_only=True, allowed_methods=["GET"]
        )
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
        await bridge.stop()
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(async_options_updated))
    return True


async def async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not ok:
        return False
    webhook.async_unregister(hass, entry.data[CONF_WEBHOOK_ID])
    bridge: PinBridge | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if bridge:
        await bridge.stop()
    return True
