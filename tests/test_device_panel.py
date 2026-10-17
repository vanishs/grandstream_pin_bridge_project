"""Check the actual WS handler with minimal HA service doubles."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from grandstream_pin_bridge.api import AuthenticationError, DeviceError


def test_device_users_websocket_authorization_and_errors():
    path = Path(__file__).parents[1] / 'custom_components/grandstream_pin_bridge/device_panel.py'
    tree = ast.parse(path.read_text())
    handler = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'websocket_users')
    handler.decorator_list = []
    device = SimpleNamespace(identifiers={('grandstream_pin_bridge', 'entry')}, config_entries={'entry'})
    client = SimpleNamespace(users=AsyncMock(return_value=[{'user_id': '35', 'username': 'n35'}]))
    namespace = {'DOMAIN': 'grandstream_pin_bridge', 'AuthenticationError': AuthenticationError, 'DeviceError': DeviceError,
                 'dr': SimpleNamespace(async_get=lambda hass: SimpleNamespace(async_get=lambda device_id: device))}
    exec(compile(ast.Module(body=[handler], type_ignores=[]), str(path), 'exec'), namespace)
    results, errors = [], []
    admin = True
    def require_admin():
        if not admin:
            raise PermissionError
    conn = SimpleNamespace(require_admin=require_admin, send_result=lambda *args: results.append(args), send_error=lambda *args: errors.append(args))
    hass = SimpleNamespace(data={'grandstream_pin_bridge': {'entry': SimpleNamespace(client=client)}})
    msg = {'id': 1, 'device_id': 'device'}
    async def run():
        nonlocal admin, device
        await namespace['websocket_users'](hass, conn, msg)
        assert results == [(1, {'users': [{'user_id': '35', 'username': 'n35'}]})]
        admin = False
        try:
            await namespace['websocket_users'](hass, conn, msg)
            assert False
        except PermissionError:
            pass
        assert client.users.await_count == 1
        admin = True
        device.config_entries.clear()
        await namespace['websocket_users'](hass, conn, msg)
        assert errors[-1][1] == 'not_loaded'
        assert client.users.await_count == 1
        device.config_entries.add('entry')
        for failure, code in [(AuthenticationError('secret response'), 'invalid_auth'), (DeviceError('secret response'), 'cannot_connect')]:
            client.users.side_effect = failure
            await namespace['websocket_users'](hass, conn, msg)
            assert errors[-1][1] == code
            assert 'secret response' not in errors[-1][2]
        device = None
        await namespace['websocket_users'](hass, conn, msg)
        assert errors[-1][1] == 'not_found'
    asyncio.run(run())
