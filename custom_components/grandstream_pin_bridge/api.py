"""Async GDS3725 Web-API client: SID cache, Challenge login and auto re-login.

Reusable transport for additional GDS features in later versions. No HA API
imports so device I/O is testable independently of Home Assistant.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlencode

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout

from .const import AUTH_SALT

CHALLENGE_RE = re.compile(r"<ChallengeCode>\s*([^<]+?)\s*</ChallengeCode>", re.I)
SID_RE = re.compile(r"<sid>\s*([^<]+?)\s*</sid>", re.I)


class DeviceError(Exception):
    """Device response was invalid or connection failed."""


class AuthenticationError(DeviceError):
    """Device credentials or SID are invalid."""


def make_authcode(challenge: str, password: str) -> str:
    """Device-specific Challenge authentication, matching tested shell flow."""
    blob = f"{challenge}:{AUTH_SALT}:{password}".encode("utf-8")
    return hashlib.md5(blob, usedforsecurity=False).hexdigest()


class GDSClient:
    """Requests are serialized by the bridge's single worker.

    The config flow uses a separate short-lived instance to verify credentials.
    """

    def __init__(
        self,
        session: ClientSession,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.sid: str | None = None
        self._login_lock = asyncio.Lock()

    async def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> str:
        extra_headers = {"Referer": f"{self.base_url}/", "Accept": "*/*"}
        if self.sid:
            extra_headers["Cookie"] = f"sid={self.sid}"
        if headers:
            extra_headers.update(headers)
        try:
            async with self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=extra_headers,
                json=body,
                ssl=self.verify_ssl,
                timeout=ClientTimeout(total=6),
                allow_redirects=False,
            ) as response:
                if response.status in (401, 403):
                    raise AuthenticationError("Device rejected session")
                if response.status >= 400 or 300 <= response.status < 400:
                    raise DeviceError(f"Device HTTP {response.status}")
                return await response.text()
        except (ClientError, TimeoutError) as exc:
            raise DeviceError(f"Device HTTP request failed: {type(exc).__name__}") from exc

    async def login(self) -> None:
        async with self._login_lock:
            self.sid = None
            challenge_response = await self._request("/goform/login?cmd=login")
            match = CHALLENGE_RE.search(challenge_response)
            if match is None:
                raise AuthenticationError("ChallengeCode missing")
            query = urlencode(
                {
                    "cmd": "login",
                    "user": self.username,
                    "authcode": make_authcode(match.group(1), self.password),
                }
            )
            result = await self._request(f"/goform/login?{query}")
            match = SID_RE.search(result)
            if match is None or not match.group(1):
                raise AuthenticationError("Device login rejected credentials")
            self.sid = match.group(1)

    async def records(self, *, page_size: int = 50) -> list[dict[str, Any]]:
        """Read newest records, re-logging once if SID expired.

        JSON failures are treated as possibly expired SID; retried once to
        distinguish session expiry from a broken device response.
        """
        if self.sid is None:
            await self.login()
        data = {
            "gdsOpenDoorRecordOpt": {
                "opt": "query",
                "page": "1",
                "page_size": str(page_size),
                "displayed_type": [],
                "time_begin": "",
                "search_value": "",
                "time_end": "",
            }
        }
        for attempt in range(2):
            try:
                raw = await self._request(
                    "/cgi-bin/api-gds_config?cmd=get&type=door_record",
                    method="POST",
                    body=data,
                    headers={
                        "Accept": "application/json",
                        "Referer": f"{self.base_url}/gds/doorRecord",
                        "X-Requested-With": "XMLHttpRequest",
                    },
                )
                try:
                    response = json.loads(raw)
                except (ValueError, TypeError) as exc:
                    raise AuthenticationError("Non-JSON response to door_record") from exc
                if not isinstance(response, dict):
                    raise DeviceError("Invalid device response")
                avs_data = response.get("avs_data") or {}
                if (
                    response.get("response") != "success"
                    or str((avs_data.get("error") or {}).get("code")) != "0"
                ):
                    raise AuthenticationError("door_record returned error; possible expired SID")
                records = avs_data.get("records")
                if not isinstance(records, list):
                    raise DeviceError("door_record records not a list")
                return records
            except AuthenticationError:
                if attempt == 1:
                    raise
                await self.login()
        raise RuntimeError("unreachable")
