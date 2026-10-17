"""Seat snapshots and persistent per-ticket entry accounting (device local time)."""
from datetime import datetime


def device_time(value):
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


class CheckIns:
    def __init__(self, state=None):
        state = state or {}
        self.seats = state.get("seats", {})
        self.orders = state.get("orders", {})
        self.history = state.get("history", [])

    def sync(self, users, synced_at):
        seats = {}
        for user in users:
            seat, ticket = user["user_id"], user["username"].strip()
            seats[seat] = {**user, "synced_at": synced_at}
            if ticket and user.get("enabled") is True:
                self.orders.setdefault(ticket, {"user_id": seat, "entry_count": 0,
                    "first_checkin_at": None, "last_entry_at": None, "event_ids": []})
        self.seats = seats

    def enter(self, event):
        seat = self.seats.get(event["user_id"])
        if not seat or seat.get("enabled") is not True:
            return None
        ticket = seat["username"].strip()
        order = self.orders.get(ticket)
        occurred = device_time(event["open_door_time"])
        start, end = device_time(seat.get("valid_from")), device_time(seat.get("valid_until"))
        if not order or order["user_id"] != event["user_id"] or not occurred or not start or not end or not start <= occurred <= end:
            return None
        if event["event_uuid"] in order["event_ids"]:
            return None
        order["event_ids"].append(event["event_uuid"])
        order["entry_count"] += 1
        order["first_checkin_at"] = order["first_checkin_at"] or event["open_door_time"]
        order["last_entry_at"] = event["open_door_time"]
        entry = {**event, "ticket_id": ticket, "entry_count": order["entry_count"]}
        self.history.append(entry)
        return entry

    def rows(self):
        rows = []
        for seat in self.seats.values():
            order = self.orders.get(seat["username"].strip(), {})
            count = order.get("entry_count", 0)
            enabled = seat.get("enabled")
            start, end = device_time(seat.get("valid_from")), device_time(seat.get("valid_until"))
            valid = (bool(seat["username"].strip()) and
                     start is not None and end is not None and start <= end and
                     order.get("user_id") == seat["user_id"])
            status = "unsold" if enabled is False else (
                "checked_in" if count else "sold"
            ) if enabled is True and valid else "unknown"
            rows.append({**seat, "status": status, "entry_count": count,
                         "first_checkin_at": order.get("first_checkin_at")})
        return rows

    def dump(self):
        return {"seats": self.seats, "orders": self.orders, "history": self.history}
