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
