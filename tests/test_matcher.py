"""Pure correlation logic tests; run with python -m pytest -q tests/."""
from __future__ import annotations
import importlib.util
from pathlib import Path

PATH = (Path(__file__).parents[1] / "custom_components" /
        "grandstream_pin_bridge" / "matcher.py")
spec = importlib.util.spec_from_file_location("gds_matcher", PATH)
matcher = importlib.util.module_from_spec(spec)
assert spec.loader
spec.loader.exec_module(matcher)


def sample(seq, uid="35"):
    return {"uuid": str(seq), "event_uuid": f"evt-{seq}", "user_name": uid}


def test_pagination_sort_dedupe():
    rows = [sample(35), sample(33), sample(34), sample(34), sample(31)]
    assert [r["uuid"] for r in matcher.records_since(rows, 32)] == ["33", "34", "35"]


def test_reject_mismatch():
    assert matcher.match_batch([sample(33)], 2) is False
    assert matcher.match_batch([sample(33), sample(34)], 1) is False


def test_reject_missing_identity():
    assert matcher.match_batch([sample(33, "")], 1) is False
    assert matcher.match_batch([{"uuid": "33", "user_name": "35"}], 1) is False


def test_accept_one_or_burst():
    assert matcher.match_batch([sample(33)], 1) is True
    assert matcher.match_batch([sample(33), sample(34)], 2) is True
