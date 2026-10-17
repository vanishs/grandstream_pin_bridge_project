"""Authenticated device-page access users endpoint and frontend registration."""
from pathlib import Path

import voluptuous as vol
from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .api import AuthenticationError, DeviceError
from .const import DOMAIN

_REGISTERED = f"{DOMAIN}_device_panel"
_URL = "/grandstream_pin_bridge_static/device-users.js"


async def async_register_device_panel(hass: HomeAssistant) -> None:
    """Register once per HA instance; survives individual entry reloads."""
    if hass.data.get(_REGISTERED):
        return
    await hass.http.async_register_static_paths([
        StaticPathConfig(_URL, str(Path(__file__).parent / "frontend/device-users.js"), False)
    ])
    websocket_api.async_register_command(hass, websocket_users)
    frontend.add_extra_js_url(hass, f"{_URL}?v=2")
    hass.data[_REGISTERED] = True


@websocket_api.websocket_command({
    vol.Required("type"): f"{DOMAIN}/access_users",
    vol.Required("device_id"): str,
})
@websocket_api.async_response
async def websocket_users(hass, connection, msg):
    """Return only public user identity fields to authenticated administrators."""
    if connection.user is None or not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Administrator access required")
        return
    device = dr.async_get(hass).async_get(msg["device_id"])
    if device is None:
        connection.send_error(msg["id"], "not_found", "Device not found")
        return
    bridges = hass.data.get(DOMAIN, {})
    bridge = next((bridges[entry_id] for domain, entry_id in device.identifiers
                   if domain == DOMAIN and entry_id in device.config_entries
                   and entry_id in bridges), None)
    if bridge is None:
        connection.send_error(msg["id"], "not_loaded", "GDS integration is not loaded")
        return
    try:
        users = await bridge.client.users()
    except AuthenticationError:
        connection.send_error(msg["id"], "invalid_auth", "Device rejected authentication or permissions")
    except (DeviceError, OSError, ValueError):
        connection.send_error(msg["id"], "cannot_connect", "Could not read device users")
    else:
        connection.send_result(msg["id"], {"users": users})
