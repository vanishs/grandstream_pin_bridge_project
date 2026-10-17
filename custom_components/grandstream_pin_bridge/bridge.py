"""Async webhook event correlation, HA Store persistence and bus events."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import urlsplit
import logging
from typing import Any
from aiohttp import ClientTimeout, ClientError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store

from .api import GDSClient
from .const import (
    CONF_FORWARD_URL,
    CONF_FORWARD_TOKEN,
    CONF_MATCH_TIMEOUT,
    CONF_POLL_INTERVAL,
    DEFAULT_MATCH_TIMEOUT,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EVENT_PIN_OPEN,
    PAGE_SIZE,
    SIGNAL_UPDATE,
)
from .matcher import match_batch, record_number, records_since

_LOGGER = logging.getLogger(__name__)


class PinBridge:
    """Runs all device calls on one task to serialize session re-logins."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        client: GDSClient,
        options: dict[str, Any],
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.client = client
        self.match_timeout = float(options.get(CONF_MATCH_TIMEOUT, DEFAULT_MATCH_TIMEOUT))
        self.poll_interval = float(options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL))
        self.queue: asyncio.Queue[None] = asyncio.Queue(maxsize=128)
        self.store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}_{entry_id}")
        self.forward_url = str(options.get(CONF_FORWARD_URL, "") or "").strip()
        self.forward_token = str(options.get(CONF_FORWARD_TOKEN, "") or "")
        self.pending_forwards: list[dict[str, Any]] = []
        self.task: asyncio.Task[None] | None = None
        self.cursor = 0
        self.last_event: dict[str, Any] = {}
        self.pin_count: int = 0
        self.recent_ids: list[str] = []
        self.host = urlsplit(client.base_url).hostname or ""

    async def start(self) -> None:
        state = await self.store.async_load() or {}
        self.last_event = state.get("last_event", {})
        self.pin_count = int(state.get("pin_count", 0))
        self.recent_ids = list(state.get("recent_ids", []))[-100:]
        self.pending_forwards = list(state.get("pending_forwards", []))[-200:]
        # Establish startup baseline BEFORE registering webhook. Old records are
        # skipped since HA was not guaranteed to receive corresponding callbacks.
        newest = await self.client.records(page_size=1)
        self.cursor = max((record_number(r) for r in newest), default=0)
        _LOGGER.info("PIN Bridge ready for %s; starting at record %d", self.host, self.cursor)
        self.task = self.hass.async_create_task(self._run(), f"{DOMAIN}_worker")

    def enqueue(self) -> bool:
        if self.task is None or self.task.done():
            return False
        try:
            self.queue.put_nowait(None)
            return True
        except asyncio.QueueFull:
            _LOGGER.error("GDS PIN event queue full; cannot accept notification")
            return False

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None

    def _drain(self) -> int:
        count = 0
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                count += 1
            except asyncio.QueueEmpty:
                return count

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self.queue.get(), timeout=10.0)
            except asyncio.TimeoutError:
                await self._forward_pending()
                continue
            self.queue.task_done()
            try:
                await asyncio.sleep(0.20)
                count = 1 + self._drain()
                _LOGGER.debug("Received %d PIN notification(s)", count)
                await self._correlate(count)
                await self._forward_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception("PIN correlation worker failed; waiting for next event")

    async def _correlate(self, count: int) -> None:
        deadline = asyncio.get_running_loop().time() + self.match_timeout
        newest: list[dict[str, Any]] = []
        # A notification can arrive before the new record is committed. Poll
        # for a short bounded window, and settle briefly after the first match.
        while True:
            try:
                records = await self.client.records(page_size=PAGE_SIZE)
                newest = records_since(records, self.cursor)
                if len(newest) >= count:
                    await asyncio.sleep(0.35)
                    count += self._drain()
                    records = await self.client.records(page_size=PAGE_SIZE)
                    newest = records_since(records, self.cursor)
                    if len(newest) == count:
                        break
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning("Unable to read GDS door_record yet: %s", err)
            if asyncio.get_running_loop().time() >= deadline:
                count += self._drain()
                break
            await asyncio.sleep(self.poll_interval)
        # A page can truncate a busy device. Any ambiguous record batch is
        # discarded instead of falsely attributing the wrong PIN user.
        overflow = bool(newest and record_number(newest[-1]) - self.cursor > PAGE_SIZE)
        valid = match_batch(newest, count) and not overflow
        if not valid:
            _LOGGER.warning(
                "Uncertain PIN attribution: %d notifications / %d new records "
                "(or missing user/event ID); no HA event fired", count, len(newest)
            )
        else:
            for record in newest:
                await self._emit(record)
        if newest:
            self.cursor = max(self.cursor, record_number(newest[-1]))
            await self._persist()

    async def _emit(self, record: dict[str, Any]) -> None:
        event_uuid = str(record["event_uuid"])
        if event_uuid in self.recent_ids:
            return
        event = {
            "user_id": str(record["user_name"]).strip(),
            "name": str(record.get("name") or ""),
            "door_id": str(record.get("door_id") or ""),
            "open_door_time": str(record.get("open_door_time") or ""),
            "record_uuid": str(record["uuid"]),
            "event_uuid": event_uuid,
            "device": self.host,
            "received_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        self.last_event = event
        self.pin_count += 1
        self.recent_ids = (self.recent_ids + [event_uuid])[-100:]
        if self.forward_url:
            if len(self.pending_forwards) >= 200:
                _LOGGER.error("Forwarding queue at 200 events; oldest unsent event discarded")
                self.pending_forwards.pop(0)
            self.pending_forwards.append(event)
        # Best-effort event correlation, NEVER a new door authorization source.
        self.hass.bus.async_fire(EVENT_PIN_OPEN, event)
        async_dispatcher_send(self.hass, SIGNAL_UPDATE.format(self.entry_id))
        _LOGGER.info(
            "GDS PIN event user_id=%s record_uuid=%s event_uuid=%s",
            event["user_id"], event["record_uuid"], event_uuid
        )
        # Save immediately so restart preserves last values / duplicate keys.
        await self._persist()

    async def _forward_pending(self) -> None:
        """Best-effort optional external JSON POST; retry pending events in order."""
        if not self.forward_url:
            return
        while self.pending_forwards:
            event = self.pending_forwards[0]
            headers = {"Accept": "application/json", "Content-Type": "application/json"}
            if self.forward_token:
                headers["Authorization"] = f"Bearer {self.forward_token}"
            try:
                async with self.client.session.post(
                    self.forward_url,
                    json={"event": "private_pin_open", **event},
                    headers=headers,
                    timeout=ClientTimeout(total=5),
                    allow_redirects=False,
                ) as resp:
                    if not 200 <= resp.status < 300:
                        raise ValueError(f"Forward server returned HTTP {resp.status}")
                    await resp.read()
            except asyncio.CancelledError:
                raise
            except (ClientError, TimeoutError, ValueError) as err:
                _LOGGER.warning("Forwarding paused for retry: %s", err)
                return
            self.pending_forwards.pop(0)
            await self._persist()

    async def _persist(self) -> None:
        await self.store.async_save({
            "cursor": self.cursor,
            "last_event": self.last_event,
            "pin_count": self.pin_count,
            "recent_ids": self.recent_ids,
            "pending_forwards": self.pending_forwards,
        })
