"""Pure record correlation helpers, separated for reuse and unit testing.

IMPORTANT: even an equal number of records and notifications is not proof of
atomic PIN-to-user attribution. This is a best-effort log correlation only.
"""
from __future__ import annotations

from typing import Any


def record_number(record: dict[str, Any]) -> int:
    return int(record["uuid"])


def records_since(records: list[dict[str, Any]], cursor: int) -> list[dict[str, Any]]:
    """Sort records older->newer and remove identical event UUIDs."""
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        number = record_number(record)
        if number > cursor:
            unique[str(record.get("event_uuid") or f"record-{number}")] = record
    return sorted(unique.values(), key=record_number)


def match_batch(records: list[dict[str, Any]], expected: int) -> bool:
    """Reject missing/extraneous records or records lacking a user identity."""
    return bool(expected > 0 and len(records) == expected and all(
        str(r.get("user_name", "")).strip() and r.get("event_uuid") for r in records
    ))
