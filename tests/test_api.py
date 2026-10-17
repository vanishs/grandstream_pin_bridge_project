"""Integration's client challenged by a fake GDS HTTP endpoint (no HA required)."""
from __future__ import annotations
import asyncio
import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import types

from aiohttp import ClientSession, web

PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "grandstream_pin_bridge"
PACKAGE = "grandstream_pin_bridge"
# Avoid executing integration __init__.py (requires Home Assistant runtime).
pkg = types.ModuleType(PACKAGE)
pkg.__path__ = [str(PACKAGE_DIR)]
sys.modules.setdefault(PACKAGE, pkg)
for module in ("const", "api"):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{module}", PACKAGE_DIR / f"{module}.py")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    assert spec.loader
    spec.loader.exec_module(loaded)
from grandstream_pin_bridge.api import GDSClient, make_authcode


def test_challenge_hash():
    expected = hashlib.md5(b"abc:GDS3710lZpRsFzCbM:secret").hexdigest()
    assert make_authcode("abc", "secret") == expected


def test_cached_sid_and_relogin():
    async def verify() -> None:
        sessions = []
        expire = {"once": True}

        async def login(request):
            if request.query.get("authcode"):
                if request.query.get("user") != "user" or request.query["authcode"] != make_authcode("ABC", "secret"):
                    return web.Response(text="<RetMsg>FAILED</RetMsg>")
                sid = f"sid{len(sessions) + 1}"
                sessions.append(sid)
                return web.Response(text=f"<sid>{sid}</sid>")
            return web.Response(text="<ChallengeCode>ABC</ChallengeCode>")

        async def records(request):
            if request.headers.get("Cookie") != f"sid={sessions[-1]}":
                return web.Response(status=403)
            if expire["once"]:
                expire["once"] = False
                return web.Response(text="<html>Session expired</html>")
            assert (await request.json())["gdsOpenDoorRecordOpt"]["page_size"] == "1"
            return web.json_response({"response":"success", "avs_data":{
                "error":{"code":0}, "records":[{"uuid":"34", "user_name":"35", "event_uuid":"evt34"}]}})

        app = web.Application()
        app.router.add_get("/goform/login", login)
        app.router.add_post("/cgi-bin/api-gds_config", records)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as session:
                client = GDSClient(session, f"http://127.0.0.1:{port}", "user", "secret")
                assert (await client.records(page_size=1))[0]["user_name"] == "35"
                assert len(sessions) == 2  # stale session detected, one re-login
                assert len(await client.records(page_size=1)) == 1
                assert len(sessions) == 2  # cached SID reused
        finally:
            await runner.cleanup()

    asyncio.run(verify())


def test_access_users_xml_and_session_retry():
    async def verify():
        logins = []
        calls = []

        async def login(request):
            if request.query.get("authcode"):
                logins.append(True)
                return web.Response(text=f"<sid>sid{len(logins)}</sid>")
            return web.Response(text="<ChallengeCode>ABC</ChallengeCode>")

        async def users(request):
            assert request.query == {"cmd": "get", "type": "user_list", "page": "1", "page_size": "100"}
            assert request.headers["Accept"] == "application/xml"
            assert request.headers["Referer"].endswith("/")
            calls.append(request.headers["Cookie"])
            if len(calls) == 1:
                return web.Response(status=403)
            return web.Response(text='''<Configuration><ResCode>0</ResCode><total>2</total>
                <item><user_id>35</user_id><username>A &amp; B</username><enable_card>1</enable_card><SOD>20261017000000</SOD><EOD>20261017235959</EOD><passwd>private-pin</passwd></item>
                <item><user_id>11</user_id><username>n11</username><card_num>private-card</card_num></item>
                </Configuration>''', content_type="application/xml")

        app = web.Application()
        app.router.add_get("/goform/login", login)
        app.router.add_get("/goform/config", users)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with ClientSession() as session:
                client = GDSClient(session, f"http://127.0.0.1:{port}", "user", "secret")
                expected = [{"user_id": "35", "username": "A & B", "enabled": True,
                             "valid_from": "20261017000000", "valid_until": "20261017235959"},
                            {"user_id": "11", "username": "n11", "enabled": None,
                             "valid_from": "", "valid_until": ""}]
                assert await client.users() == expected
                assert await client.users() == expected
                assert calls == ["sid=sid1", "sid=sid2", "sid=sid2"]
                assert len(logins) == 2
        finally:
            await runner.cleanup()
    asyncio.run(verify())


def test_shared_request_error_policy_and_recovery():
    from unittest.mock import AsyncMock
    from grandstream_pin_bridge.api import AuthenticationError, DeviceError, InvalidResponseError

    async def verify():
        good = '<Configuration><ResCode>0</ResCode></Configuration>'
        for failure in (AuthenticationError('expired'), InvalidResponseError('login page')):
            client = GDSClient(None, 'https://device', 'user', 'secret')
            client.sid = 'old'
            async def login():
                client.sid = 'new'
            client._login = AsyncMock(side_effect=login)
            client._request = AsyncMock(side_effect=[failure, good])
            result = await client.request('/goform/config', response_format='xml')
            assert result.tag == 'Configuration'
            assert client._login.await_count == 1
            assert client._request.await_count == 2

        client._request = AsyncMock(side_effect=AuthenticationError('denied'))
        client._login.reset_mock()
        try:
            await client.request('/goform/config', response_format='xml')
            assert False, 'Must propagate persistent rejection'
        except AuthenticationError:
            pass
        assert client._login.await_count == 1
        assert client._request.await_count == 2
        assert client.sid is None

        for failure in (DeviceError('HTTP 500'), DeviceError('timeout')):
            client.sid = 'valid'
            client._login.reset_mock()
            client._request = AsyncMock(side_effect=failure)
            try:
                await client.request('/goform/config')
                assert False, 'Must propagate transport/server errors'
            except DeviceError:
                pass
            assert client._login.await_count == 0
            assert client._request.await_count == 1
        client._request = AsyncMock(return_value=good)
        assert (await client.request('/goform/config', response_format='xml')).tag == 'Configuration'
    asyncio.run(verify())


def test_shared_response_validation():
    import pytest
    from grandstream_pin_bridge.api import AuthenticationError, InvalidResponseError
    for raw in ('null', '[]', '{', '{"avs_data": []}', '{"avs_data": {"error": []}}'):
        with pytest.raises(InvalidResponseError):
            GDSClient._parse_response(raw, 'json')
    for raw in ('<html>Login</html>', '<Configuration>', '<Configuration/>'):
        with pytest.raises(InvalidResponseError):
            GDSClient._parse_response(raw, 'xml')
    for raw, fmt in [('<Configuration><ResCode>1</ResCode></Configuration>', 'xml'),
                     ('{"response":"error","avs_data":{"error":{"code":1}}}', 'json')]:
        with pytest.raises(AuthenticationError):
            GDSClient._parse_response(raw, fmt)


def test_shared_request_serializes_and_releases_on_cancel():
    from unittest.mock import AsyncMock
    async def verify():
        client = GDSClient(None, 'https://device', 'user', 'secret')
        client.sid = 'valid'
        started = asyncio.Event()
        release = asyncio.Event()
        async def blocked(*args, **kwargs):
            started.set()
            await release.wait()
            return '<Configuration><ResCode>0</ResCode></Configuration>'
        client._request = AsyncMock(side_effect=blocked)
        first = asyncio.create_task(client.request('/one', response_format='xml'))
        await started.wait()
        second = asyncio.create_task(client.request('/two', response_format='xml'))
        await asyncio.sleep(0)
        assert client._request.await_count == 1
        first.cancel()
        try:
            await first
            assert False, 'Cancellation must propagate'
        except asyncio.CancelledError:
            pass
        release.set()
        assert (await second).tag == 'Configuration'
        assert client._request.await_count == 2
    asyncio.run(verify())
