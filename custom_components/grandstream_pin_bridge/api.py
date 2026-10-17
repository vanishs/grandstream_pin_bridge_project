"""Async GDS3725 Web-API client: SID cache, Challenge login and auto re-login.

Reusable transport for additional GDS features in later versions. No HA API
imports so device I/O is testable independently of Home Assistant.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from xml.etree import ElementTree as ET
from typing import Any, Literal
from urllib.parse import urlencode

from aiohttp import ClientError, ClientSession, ClientTimeout

from .const import AUTH_SALT

CHALLENGE_RE = re.compile(r"<ChallengeCode>\s*([^<]+?)\s*</ChallengeCode>", re.I)
SID_RE = re.compile(r"<sid>\s*([^<]+?)\s*</sid>", re.I)


class DeviceError(Exception):
    """Device response was invalid or connection failed."""


class InvalidResponseError(DeviceError):
    """Device returned a malformed response (possibly an expired-session page)."""


class AuthenticationError(DeviceError):
    """Device credentials or SID are invalid."""


def make_authcode(challenge: str, password: str) -> str:
    """Device-specific Challenge authentication, matching tested shell flow."""
    blob = f"{challenge}:{AUTH_SALT}:{password}".encode("utf-8")
    return hashlib.md5(blob, usedforsecurity=False).hexdigest()


class GDSClient:
    """Device reads are serialized to share the cached SID safely.

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
        self._operation_lock = asyncio.Lock()

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
                try:
                    return await response.text()
                except UnicodeError:
                    raise InvalidResponseError("Device response encoding is invalid") from None
        except (ClientError, TimeoutError) as exc:
            raise DeviceError(f"Device HTTP request failed: {type(exc).__name__}") from exc

    async def login(self) -> None:
        """Explicit login shares the operation lock with authenticated requests."""
        async with self._operation_lock:
            await self._login()

    async def _login(self) -> None:
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


    @staticmethod
    def _parse_response(raw: str, response_format: Literal["json", "xml"]) -> dict | ET.Element:
        """Validate common GDS success envelopes without exposing response bodies."""
        if response_format == "xml":
            try:
                root = ET.fromstring(raw)
            except ET.ParseError:
                raise InvalidResponseError("Invalid device XML response") from None
            if root.tag != "Configuration" or root.findtext("ResCode") is None:
                raise InvalidResponseError("Missing device XML result")
            if root.findtext("ResCode").strip() != "0":
                raise AuthenticationError("Device rejected API request or session")
            return root
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise InvalidResponseError("Invalid device JSON response") from None
        if not isinstance(data, dict):
            raise InvalidResponseError("Device JSON response is not an object")
        payload = data.get("avs_data")
        if not isinstance(payload, dict):
            raise InvalidResponseError("Missing device JSON payload")
        error = payload.get("error")
        if not isinstance(error, dict) or "code" not in error:
            raise InvalidResponseError("Missing device JSON result")
        if data.get("response") != "success" or str(error["code"]) != "0":
            raise AuthenticationError("Device rejected API request or session")
        return data

    async def request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        response_format: Literal["json", "xml"] = "json",
    ) -> dict | ET.Element:
        """Single entry point for all authenticated GDS APIs.

        Serialize the full login/request/retry transaction. Retry once after
        authentication rejection or an invalid response (GDS may return an HTML
        login page with HTTP 200). Transport/HTTP errors are not replayed: their
        outcome may be unknown, especially for future write operations. A replay
        after an explicit session rejection assumes the operation was not applied.
        Cancellation propagates naturally and releases the operation lock.
        """
        if response_format not in ("json", "xml"):
            raise ValueError("Unsupported device response format")
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("Device API path must be relative to the device")
        async with self._operation_lock:
            if self.sid is None:
                await self._login()
            for attempt in range(2):
                try:
                    raw = await self._request(path, method=method, body=body, headers=headers)
                    return self._parse_response(raw, response_format)
                except (AuthenticationError, InvalidResponseError):
                    self.sid = None
                    if attempt == 1:
                        raise
                    await self._login()
        raise RuntimeError("unreachable")

    async def users(self) -> list[dict[str, str]]:
        """Fetch at most 100 access users; discard all other device fields."""
        root = await self.request(
            "/goform/config?cmd=get&type=user_list&page=1&page_size=100",
            headers={"Accept": "application/xml"}, response_format="xml",
        )
        items = root.findall("item")
        if len(items) > 100:
            raise DeviceError("user_list exceeds 100 users")
        users = []
        for item in items:
            user_id = item.findtext("user_id")
            username = item.findtext("username")
            if not user_id or username is None:
                raise DeviceError("user_list is missing user identity")
            users.append({"user_id": user_id, "username": username})
        return users

    async def records(self, *, page_size: int = 50) -> list[dict[str, Any]]:
        """Read newest records through the shared authenticated request method."""
        data = {
            "gdsOpenDoorRecordOpt": {
                "opt": "query", "page": "1", "page_size": str(page_size),
                "displayed_type": [], "time_begin": "", "search_value": "", "time_end": "",
            }
        }
        response = await self.request(
            "/cgi-bin/api-gds_config?cmd=get&type=door_record",
            method="POST", body=data,
            headers={
                "Accept": "application/json", "Referer": f"{self.base_url}/gds/doorRecord",
                "X-Requested-With": "XMLHttpRequest",
            },
        )
        records = response["avs_data"].get("records")
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise DeviceError("door_record records not a list of objects")
        return records
