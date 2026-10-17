"""Minimal HA sensors; room for future device platforms is preserved."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import PinBridge
from .const import DOMAIN, SIGNAL_UPDATE


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge: PinBridge = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        LastPinUserSensor(bridge, entry),
        PinCountSensor(bridge, entry),
    ])


class BaseGDSBridgeSensor(SensorEntity):
    """Reusable base for future GDS sensor extensions."""
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, bridge: PinBridge, entry: ConfigEntry) -> None:
        self.bridge = bridge
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"GDS3725 {bridge.host}",
            manufacturer="Grandstream",
            model="GDS3725",
            configuration_url=bridge.client.base_url,
        )
        self._attr_unique_id = f"{entry.entry_id}_{self._key}"
        self._attr_translation_key = self._key
        self._signal = SIGNAL_UPDATE.format(entry.entry_id)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self._signal, self._handle_update
        ))

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class LastPinUserSensor(BaseGDSBridgeSensor):
    _key = "last_pin_user"
    _attr_icon = "mdi:account-key"

    @property
    def native_value(self) -> str | None:
        return self.bridge.last_event.get("user_id")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.bridge.last_event
        return {key: data.get(key) for key in (
            "name", "door_id", "open_door_time", "record_uuid", "event_uuid", "device"
        ) if key in data}


class PinCountSensor(BaseGDSBridgeSensor):
    _key = "pin_events"
    _attr_icon = "mdi:counter"

    @property
    def native_value(self) -> int:
        return self.bridge.pin_count
