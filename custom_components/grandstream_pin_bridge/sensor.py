"""Minimal HA sensors; room for future device platforms is preserved."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.components.logbook import async_log_entry
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .bridge import PinBridge
from .const import DOMAIN, SIGNAL_UPDATE


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    bridge: PinBridge = hass.data[DOMAIN][entry.entry_id]
    registry = er.async_get(hass)
    obsolete = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_pin_events")
    if obsolete:
        registry.async_remove(obsolete)
    async_add_entities([LastPinUserSensor(bridge, entry)])


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
    @callback
    def _handle_update(self) -> None:
        super()._handle_update()
        event = self.bridge.last_event
        if event.get("ticket_id") and event.get("event_uuid") != getattr(self, "_logged_event", None):
            self._logged_event = event["event_uuid"]
            async_log_entry(
                self.hass, "入场",
                f"订单 {event['ticket_id']} · 座位 {event['user_id']} · 第 {event['entry_count']} 次入场",
                domain=DOMAIN, entity_id=self.entity_id,
            )

    _key = "last_pin_user"
    _attr_icon = "mdi:account-key"

    @property
    def native_value(self) -> str | None:
        return self.bridge.last_event.get("user_id")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.bridge.last_event
        return {key: data.get(key) for key in (
            "name", "door_id", "open_door_time", "record_uuid", "event_uuid", "device",
            "ticket_id", "entry_count"
        ) if key in data}
