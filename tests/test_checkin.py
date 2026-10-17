"""Seat status and ticket accounting rules, independent of HA."""
import copy
import importlib.util
from pathlib import Path

PATH = Path(__file__).parents[1] / 'custom_components/grandstream_pin_bridge/checkin.py'
spec = importlib.util.spec_from_file_location('gds_checkin', PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
CheckIns = module.CheckIns


def user(ticket='order-A', enabled=True, start='20261017080000', end='20261017200000'):
    return {'user_id': '35', 'username': ticket, 'enabled': enabled,
            'valid_from': start, 'valid_until': end}


def event(event_id='e1', time='2026-10-17 09:12:00'):
    return {'user_id': '35', 'event_uuid': event_id, 'open_door_time': time}


def test_sale_checkin_reentry_disable_and_new_order():
    tracker = CheckIns()
    tracker.sync([user(enabled=False)], 'sync1')
    assert tracker.rows()[0]['status'] == 'unsold'
    assert tracker.enter(event()) is None
    tracker.sync([user()], 'sync2')
    assert tracker.rows()[0]['status'] == 'sold'
    assert tracker.rows()[0]['entry_count'] == 0
    assert tracker.enter(event())['entry_count'] == 1
    assert tracker.rows()[0]['status'] == 'checked_in'
    assert tracker.enter(event()) is None
    assert tracker.enter(event('e2', '2026-10-17 12:00:00'))['entry_count'] == 2
    tracker.sync([user()], 'refresh')
    assert tracker.rows()[0]['entry_count'] == 2
    tracker.sync([user(enabled=False)], 'disabled')
    assert tracker.rows()[0]['status'] == 'unsold'
    tracker.sync([user()], 'reenabled')
    assert tracker.rows()[0]['entry_count'] == 2
    tracker.sync([user('order-B', start='20261018080000', end='20261018200000')], 'next day')
    assert tracker.rows()[0]['status'] == 'sold'
    assert tracker.rows()[0]['entry_count'] == 0
    assert tracker.enter(event('late-A')) is None
    assert tracker.enter(event('b1', '2026-10-18 09:00:00'))['entry_count'] == 1
    assert tracker.orders['order-A']['entry_count'] == 2
    assert [row['ticket_id'] for row in tracker.history] == ['order-A', 'order-A', 'order-B']


def test_persistence_unknown_and_outside_validity():
    tracker = CheckIns()
    tracker.sync([user()], 'sync')
    for timestamp in ('2026-10-17 07:59:59', '2026-10-17 20:00:01', '', 'not a time'):
        assert tracker.enter(event(time=timestamp)) is None
    tracker.enter(event())
    restored = CheckIns(copy.deepcopy(tracker.dump()))
    assert restored.enter(event()) is None
    assert restored.rows()[0]['entry_count'] == 1
    assert restored.rows()[0]['first_checkin_at'] == '2026-10-17 09:12:00'
    for malformed in (user(ticket=''), user(enabled=None), user(start='invalid')):
        restored.sync([malformed], 'bad')
        assert restored.rows()[0]['status'] == 'unknown'
        assert restored.enter(event('bad')) is None
    restored.sync([], 'removed')
    assert restored.rows() == []
    assert restored.orders['order-A']['entry_count'] == 1
