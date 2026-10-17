#!/usr/bin/env python3
"""GDS3725 Private PIN -> local HTTP receiver -> door_record -> optional JSON webhook.

Standard library only. Python >= 3.9.

Environment variables (see README instructions below):
    GDS_BASE          https://192.168.9.230
    GDS_USER          user
    GDS_PASSWORD      required
    GDS_VERIFY_TLS    0 (use 1 with a trusted device certificate)
    LISTEN_HOST       0.0.0.0
    LISTEN_PORT       8088
    DEVICE_IP         192.168.9.230 (default: host from GDS_BASE)
    INBOUND_TOKEN     optional static query token in P15529 URL
    FORWARD_URL       optional downstream HTTP(S) POST endpoint
    FORWARD_TOKEN     optional Bearer token for downstream requests
    DB_PATH           ./gds3725_events.sqlite3
    MATCH_TIMEOUT     6.0 seconds
    POLL_INTERVAL     0.25 seconds

Caution: correlation is only unambiguous if exactly one *new* door record
appears per PIN notification in a short window. If counts disagree, the
program skips the batch instead of assigning a potentially wrong user ID.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue
import re
import signal
import sqlite3
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

LOG = logging.getLogger("gds-pin")
GDS_SALT = "GDS3710lZpRsFzCbM"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    def __init__(self) -> None:
        self.base = os.getenv("GDS_BASE", "https://192.168.9.230").rstrip("/")
        self.username = os.getenv("GDS_USER", "user")
        self.password = os.getenv("GDS_PASSWORD", "")
        self.verify_tls = env_bool("GDS_VERIFY_TLS", False)
        self.host = os.getenv("LISTEN_HOST", "0.0.0.0")
        self.port = int(os.getenv("LISTEN_PORT", "8088"))
        self.device_ip = os.getenv("DEVICE_IP", urllib.parse.urlparse(self.base).hostname or "")
        self.inbound_token = os.getenv("INBOUND_TOKEN", "")
        self.forward_url = os.getenv("FORWARD_URL", "")
        self.forward_token = os.getenv("FORWARD_TOKEN", "")
        self.db_path = Path(os.getenv("DB_PATH", "./gds3725_events.sqlite3"))
        self.match_timeout = float(os.getenv("MATCH_TIMEOUT", "6"))
        self.poll_interval = float(os.getenv("POLL_INTERVAL", "0.25"))
        self.page_size = 50
        if not self.password:
            raise ValueError("GDS_PASSWORD must be set (password for the Web 'user' account)")
        if urllib.parse.urlparse(self.base).scheme not in {"http", "https"}:
            raise ValueError("GDS_BASE must start with http:// or https://")
        if self.forward_url and urllib.parse.urlparse(self.forward_url).scheme not in {"http", "https"}:
            raise ValueError("FORWARD_URL must start with http:// or https://")


class AuthError(Exception):
    pass


class GDSClient:
    def __init__(self, config: Settings):
        self.config = config
        self.sid: str | None = None
        self.ssl_context = None
        if self.config.base.startswith("https://") and not self.config.verify_tls:
            self.ssl_context = ssl._create_unverified_context()  # mirrors curl -k

    def _request(self, path: str, *, method: str = "GET", body: bytes | None = None,
                 headers: dict[str, str] | None = None) -> bytes:
        hdr = {"Referer": self.config.base + "/", "Accept": "*/*"}
        if self.sid:
            hdr["Cookie"] = "sid=" + self.sid
        if headers:
            hdr.update(headers)
        req = urllib.request.Request(self.config.base + path, data=body, headers=hdr, method=method)
        try:
            with urllib.request.urlopen(req, timeout=5, context=self.ssl_context) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthError("device rejected SID") from exc
            raise

    def login(self) -> None:
        self.sid = None
        xml1 = self._request("/goform/login?cmd=login").decode("utf-8", "replace")
        match = re.search(r"<ChallengeCode>\s*([^<]+)\s*</ChallengeCode>", xml1, flags=re.I)
        if not match:
            raise AuthError("ChallengeCode missing in device login response")
        challenge = match.group(1).strip()
        secret = f"{challenge}:{GDS_SALT}:{self.config.password}".encode("utf-8")
        authcode = hashlib.md5(secret).hexdigest()
        query = urllib.parse.urlencode({"cmd": "login", "user": self.config.username, "authcode": authcode})
        xml2 = self._request("/goform/login?" + query).decode("utf-8", "replace")
        sid_match = re.search(r"<sid>\s*([^<]+)\s*</sid>", xml2, flags=re.I)
        if not sid_match:
            raise AuthError("Challenge login failed (no sid in response)")
        self.sid = sid_match.group(1).strip()
        LOG.info("Authenticated to device with account %s", self.config.username)

    def records(self) -> list[dict[str, Any]]:
        """Use cached SID first, re-login and retry once on a rejected session."""
        if self.sid is None:
            self.login()
        for attempt in range(2):
            body = json.dumps({"gdsOpenDoorRecordOpt": {
                "opt": "query", "page": "1", "page_size": str(self.config.page_size),
                "displayed_type": [], "time_begin": "", "search_value": "", "time_end": "",
            }}).encode("utf-8")
            try:
                raw = self._request("/cgi-bin/api-gds_config?cmd=get&type=door_record",
                                    method="POST", body=body, headers={
                                        "Accept": "application/json",
                                        "Content-Type": "application/json",
                                        "Referer": self.config.base + "/gds/doorRecord",
                                        "X-Requested-With": "XMLHttpRequest",
                                    })
                try:
                    data = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AuthError("device returned non-JSON result") from exc
                avs = data.get("avs_data") or {}
                error = avs.get("error") or {}
                if data.get("response") != "success" or str(error.get("code", -1)) != "0":
                    raise AuthError("door_record API did not return success")
                records = avs.get("records", [])
                if not isinstance(records, list):
                    raise ValueError("door_record.records must be an array")
                return records
            except AuthError:
                if attempt == 1:
                    raise
                LOG.warning("SID rejected or stale; running Challenge login again")
                self.login()
        raise AssertionError("unreachable")


class PinBridge:
    def __init__(self, config: Settings):
        self.cfg = config
        self.client = GDSClient(config)
        self.notifications: queue.Queue[str] = queue.Queue(maxsize=128)
        self.db = sqlite3.connect(config.db_path, check_same_thread=False)  # only worker uses it after init
        self.db.execute("""CREATE TABLE IF NOT EXISTS events (
          event_uuid TEXT PRIMARY KEY,
          record_uuid INTEGER NOT NULL,
          data TEXT NOT NULL,
          forwarded INTEGER NOT NULL DEFAULT 0
        )""")
        self.db.commit()
        self.cursor: int = 0

    @staticmethod
    def record_id(record: dict[str, Any]) -> int:
        return int(record["uuid"])

    def initialize(self) -> None:
        # Cold start: don't misreport historical records as fresh PIN notifications.
        current = self.client.records()
        self.cursor = max((self.record_id(r) for r in current), default=0)
        LOG.info("Started at device door_record uuid=%d (historical entries ignored)", self.cursor)

    def _poll_new(self, expected: int) -> list[dict[str, Any]]:
        deadline = time.monotonic() + self.cfg.match_timeout
        newest_seen: list[dict[str, Any]] = []
        while True:
            try:
                records = self.client.records()
                newest_seen = sorted((r for r in records if self.record_id(r) > self.cursor),
                                     key=self.record_id)
                if len(newest_seen) >= expected:
                    # Allow a brief settling period so another record in the same burst
                    # can be detected before making a potentially wrong attribution.
                    time.sleep(0.25)
                    records = self.client.records()
                    newest_seen = sorted((r for r in records if self.record_id(r) > self.cursor),
                                         key=self.record_id)
                    return newest_seen
            except Exception as exc:
                LOG.warning("Query while awaiting record failed: %s", exc)
            if time.monotonic() >= deadline:
                return newest_seen
            time.sleep(self.cfg.poll_interval)

    def _save(self, record: dict[str, Any]) -> None:
        uid = str(record.get("user_name", "")).strip()
        if not uid:
            LOG.warning("Record uuid=%s has no user_name; not forwarding", record.get("uuid"))
            return
        event_id = str(record.get("event_uuid") or f"record-{record['uuid']}")
        data = {
            "event": "private_pin_open",
            "user_id": uid,
            "name": record.get("name", ""),
            "door_id": record.get("door_id", ""),
            "open_door_time": record.get("open_door_time", ""),
            "record_uuid": str(record["uuid"]),
            "event_uuid": event_id,
            "device": urllib.parse.urlparse(self.cfg.base).hostname,
            "received_at": now_iso(),
        }
        delivered = 0 if self.cfg.forward_url else 1
        cur = self.db.execute("INSERT OR IGNORE INTO events (event_uuid, record_uuid, data, forwarded)"
                              " VALUES (?, ?, ?, ?)", (event_id, self.record_id(record),
                                                        json.dumps(data, ensure_ascii=False), delivered))
        self.db.commit()
        if cur.rowcount:
            LOG.info("PIN OPEN user_id=%s name=%s uuid=%s time=%s event_uuid=%s",
                     uid, data["name"], data["record_uuid"], data["open_door_time"], event_id)
        else:
            LOG.info("Duplicate event_uuid=%s ignored", event_id)

    def _forward_pending(self) -> None:
        if not self.cfg.forward_url:
            return
        for event_id, payload in self.db.execute("SELECT event_uuid, data FROM events WHERE forwarded=0 ORDER BY record_uuid LIMIT 20").fetchall():
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if self.cfg.forward_token:
                headers["Authorization"] = "Bearer " + self.cfg.forward_token
            req = urllib.request.Request(self.cfg.forward_url, method="POST", data=payload.encode(), headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    resp.read()
                self.db.execute("UPDATE events SET forwarded=1 WHERE event_uuid=?", (event_id,))
                self.db.commit()
                LOG.info("Forwarded PIN event %s", event_id)
            except Exception as exc:
                LOG.warning("Forward failed for %s; will retry later: %s", event_id, exc)
                break  # FIFO order; retry on the next worker cycle

    def run(self) -> None:
        while True:
            try:
                self.notifications.get(timeout=5)
            except queue.Empty:
                self._forward_pending()
                continue
            count = 1
            time.sleep(0.20)  # collect near-simultaneous PIN notifications
            while True:
                try:
                    self.notifications.get_nowait()
                    count += 1
                except queue.Empty:
                    break
            LOG.info("Processing %d PIN notification(s)", count)
            try:
                records = self._poll_new(count)
                if len(records) == count:
                    for record in records:
                        self._save(record)
                    self.cursor = max(self.cursor, self.record_id(records[-1]))
                else:
                    # A card/other door action might have interleaved, or the record may
                    # not have committed yet. Never claim an uncertain user ID.
                    LOG.warning("Uncertain event match: %d PIN notifications vs %d new door records; "
                                "skipping attribution", count, len(records))
                    if records:
                        self.cursor = max(self.cursor, self.record_id(records[-1]))
                self._forward_pending()
            except Exception:
                LOG.exception("Unexpected worker failure; will keep running")
            finally:
                for _ in range(count):
                    self.notifications.task_done()


class Handler(BaseHTTPRequestHandler):
    server: "BridgeHTTPServer"

    def do_GET(self) -> None:
        url = urllib.parse.urlsplit(self.path)
        if url.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"OK\n")
            return
        if url.path != "/pin-open":
            self.send_error(404, "Not found")
            return
        cfg = self.server.bridge.cfg
        if cfg.device_ip and self.client_address[0] != cfg.device_ip:
            self.send_error(403, "Unexpected sender")
            return
        params = urllib.parse.parse_qs(url.query)
        if cfg.inbound_token and not hmac.compare_digest(params.get("token", [""])[0], cfg.inbound_token):
            self.send_error(403, "Invalid token")
            return
        try:
            self.server.bridge.notifications.put_nowait(now_iso())
        except queue.Full:
            self.send_error(503, "Queue full")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"OK\n")

    def log_message(self, format: str, *args: object) -> None:
        LOG.debug("HTTP %s - %s", self.client_address[0], format % args)


class BridgeHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], bridge: PinBridge):
        super().__init__(address, Handler)
        self.bridge = bridge


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = Settings()
        bridge = PinBridge(cfg)
        bridge.initialize()
    except Exception as exc:
        LOG.error("Initialization failed: %s", exc)
        raise SystemExit(1) from exc
    if not cfg.verify_tls and cfg.base.startswith("https://"):
        LOG.warning("GDS HTTPS certificate verification disabled; set GDS_VERIFY_TLS=1 if the certificate is trusted")
    thread = threading.Thread(target=bridge.run, daemon=True, name="gds-record-worker")
    thread.start()
    server = BridgeHTTPServer((cfg.host, cfg.port), bridge)
    LOG.info("Listening on http://%s:%d/pin-open (allowed device IP: %s)", cfg.host, cfg.port, cfg.device_ip)
    if cfg.forward_url:
        LOG.info("Confirmed events will be POSTed as JSON to configured FORWARD_URL")
    else:
        LOG.info("FORWARD_URL not set; confirmed events will be logged only")

    def on_signal(signum: int, _frame: object) -> None:
        LOG.info("Signal %s received, shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
