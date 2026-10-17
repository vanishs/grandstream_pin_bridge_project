"""The sensor emits order activity linked to its real entity ID."""
import ast
from pathlib import Path
from types import SimpleNamespace


def test_order_activity_links_device_entity_and_deduplicates():
    path = Path(__file__).parents[1] / 'custom_components/grandstream_pin_bridge/sensor.py'
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LastPinUserSensor')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == '_handle_update')
    method.decorator_list = []
    calls = []
    class Base:
        def _handle_update(self):
            pass
    cls.bases = [ast.Name(id='Base', ctx=ast.Load())]
    cls.body = [method]
    namespace = {'Base': Base, 'DOMAIN': 'grandstream_pin_bridge',
                 'async_log_entry': lambda *args, **kwargs: calls.append((args, kwargs))}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(path), 'exec'), namespace)
    sensor = namespace['LastPinUserSensor']()
    sensor.hass = object()
    sensor.entity_id = 'sensor.gds_last_pin_user'
    sensor.bridge = SimpleNamespace(last_event={'ticket_id': 'ORDER-A', 'user_id': '35', 'event_uuid': 'e1',
        'entry_count': 1, 'open_door_time': '2026-10-17 09:12:00'})
    sensor._handle_update()
    sensor._handle_update()
    assert len(calls) == 1
    assert calls[0][0][1] == '入场'
    assert calls[0][0][2] == '订单 ORDER-A · 座位 35 · 第 1 次入场'
    assert '2026-10-17' not in calls[0][0][2]
    assert '座位 35 · 第 1 次入场' in calls[0][0][2]
    assert calls[0][1]['entity_id'] == sensor.entity_id
    sensor.bridge.last_event = {'user_id': '35'}
    sensor._handle_update()
    assert len(calls) == 1
