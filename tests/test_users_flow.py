"""Exercise the users options step without installing Home Assistant Core."""
import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

from grandstream_pin_bridge.api import AuthenticationError, DeviceError


def load_step():
    path = Path(__file__).parents[1] / 'custom_components/grandstream_pin_bridge/config_flow.py'
    tree = ast.parse(path.read_text())
    flow = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'GDS3725PINOptionsFlow')
    step = next(n for n in flow.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'async_step_users')
    namespace = {'DeviceError': DeviceError, 'AuthenticationError': AuthenticationError,
                 'DOMAIN': 'grandstream_pin_bridge', 'vol': SimpleNamespace(Schema=lambda value: value)}
    exec(compile(ast.Module(body=[step], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['async_step_users']


def test_users_panel_queries_again_and_clears_failed_results():
    async def run():
        calls = []
        async def users():
            calls.append(True)
            if len(calls) == 3:
                raise AuthenticationError('Denied')
            return [{'user_id': '35', 'username': '<script>|[name](url)\nnext'}]
        flow = SimpleNamespace(
            hass=SimpleNamespace(data={'grandstream_pin_bridge': {'entry': SimpleNamespace(client=SimpleNamespace(users=users))}}),
            _entry=SimpleNamespace(entry_id='entry'), async_show_form=lambda **kwargs: kwargs,
        )
        step = load_step()
        first = await step(flow)
        assert first['description_placeholders']['count'] == '1'
        rows = first['description_placeholders']['users']
        assert '<script>' not in rows and '[name]' not in rows and '\n' not in rows
        assert '&#124;' in rows
        await step(flow, {})  # Refresh button, not a saved options update.
        failed = await step(flow, {})
        assert len(calls) == 3
        assert failed['errors'] == {'base': 'invalid_auth'}
        assert failed['description_placeholders']['users'] == ''
        flow.hass.data.clear()
        unloaded = await step(flow)
        assert unloaded['errors'] == {'base': 'cannot_connect'}
    asyncio.run(run())
