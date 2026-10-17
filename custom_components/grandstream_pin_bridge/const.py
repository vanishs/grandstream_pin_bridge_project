"""Constants for GDS3725 Private PIN event integration.

Designed using the Grandstream Home custom component layout, but intentionally
uses a separate domain to avoid clobbering an existing official installation.
"""
from __future__ import annotations

DOMAIN = "grandstream_pin_bridge"
EVENT_PIN_OPEN = "grandstream_pin_bridge_pin_open"
SIGNAL_UPDATE = f"{DOMAIN}_update_{{}}"
DEVICE_MODEL = "GDS3725"

CONF_USE_HTTPS = "use_https"
CONF_VERIFY_SSL = "verify_ssl"
CONF_MATCH_TIMEOUT = "match_timeout"
CONF_POLL_INTERVAL = "poll_interval"
CONF_WEBHOOK_ID = "webhook_id"
CONF_NEW_PASSWORD = "new_password"
CONF_FORWARD_URL = "forward_url"
CONF_FORWARD_TOKEN = "forward_token"
CONF_NEW_FORWARD_TOKEN = "new_forward_token"

DEFAULT_HOST = "192.168.9.230"
DEFAULT_PORT = 443
DEFAULT_USERNAME = "user"  # Standard GDS Web User;
DEFAULT_MATCH_TIMEOUT = 6.0
DEFAULT_POLL_INTERVAL = 0.25
PAGE_SIZE = 50
AUTH_SALT = "GDS3710lZpRsFzCbM"  # GDS API Challenge algorithm; NOT a password.
