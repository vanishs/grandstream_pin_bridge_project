"""Exercise HA event emission using tiny fake HA services (HAOS not installed)."""
from __future__ import annotations

import asyncio
import copy
import importlib.util
from pathlib import Path
import sys
import types

ROOT = Path(__file__).parents[1] / "custom_components" / "grandstream_pin_bridge"
PKG = "grandstream_pin_bridge"
if PKG not in sys.modules:
    pkg = types.ModuleType(PKG)
    pkg.__path__ = [str(ROOT)]
    sys.modules[PKG] = pkg

# Only dependencies needed by bridge.py; no real HA installation in tests.
for name in ("homeassistant", "homeassistant.core", "homeassistant.helpers",
             "homeassistant.helpers.dispatcher", "homeassistant.helpers.storage"):
    sys.modules.setdefault(name, types.ModuleType(name))

sys.modules["homeassistant.core"].HomeAssistant = object
sys.modules["homeassistant.helpers.dispatcher"].async_dispatcher_send = lambda *a: None

SAVED = {}
class FakeStore:
    def __init__(self, hass, version, key):
        self.key = key
    async def async_load(self):
        return copy.deepcopy(SAVED.get(self.key))
    async def async_save(self, value):
        SAVED[self.key] = copy.deepcopy(value)

sys.modules["homeassistant.helpers.storage"].Store = FakeStore
for name in ("const", "matcher", "api", "bridge"):
    full = f"{PKG}.{name}"
    if full in sys.modules:
        continue
    spec = importlib.util.spec_from_file_location(full, ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
from grandstream_pin_bridge.bridge import PinBridge

class FakeHass:
    def __init__(self):
        self.bus = self
        self.events = []
    def async_fire(self, name, data):
        self.events.append((name, data))
    def async_create_task(self, coro, name):
        return asyncio.create_task(coro, name=name)

class FakeDevice:
    base_url = "https://192.168.9.230"
    def __init__(self):
        self.data = [{"uuid":"33", "event_uuid":"ev33", "user_name":"35"}]
    async def records(self, page_size=50):
        return self.data[:page_size]


def test_private_pin_creates_ha_event_and_persists():
    async def run():
        SAVED.clear()
        hass = FakeHass()
        device = FakeDevice()
        bridge = PinBridge(hass, "sample", device, {"match_timeout": 2, "poll_interval": 0.1})
        await bridge.start()
        assert bridge.cursor == 33
        device.data.insert(0, {"uuid": "34", "event_uuid": "evt34",
                               "user_name":"35", "name":"n35", "door_id":"1"})
        assert bridge.enqueue()
        await asyncio.sleep(0.8)
        assert len(hass.events) == 1
        assert hass.events[0][1]["user_id"] == "35"
        assert bridge.pin_count == 1
        await bridge.stop()
        new = PinBridge(hass, "sample", device, {})
        await new.start()
        assert new.pin_count == 1
        assert new.cursor == 34
        assert len(hass.events) == 1
        await new.stop()
    asyncio.run(run())


def test_ambiguous_extra_record_emits_nothing():
    async def run():
        SAVED.clear()
        hass = FakeHass()
        device = FakeDevice()
        bridge = PinBridge(hass, "sample2", device, {"match_timeout": .2, "poll_interval": .1})
        await bridge.start()
        device.data.insert(0, {"uuid":"34", "event_uuid":"evt34", "user_name":"35"})
        device.data.insert(0, {"uuid":"35", "event_uuid":"evt35", "user_name":"36"})
        bridge.enqueue()
        await asyncio.sleep(0.85)
        assert hass.events == []
        assert bridge.cursor == 35
        await bridge.stop()
    asyncio.run(run())
