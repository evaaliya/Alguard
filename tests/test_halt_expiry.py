"""
1.2 — an expired (unresolved) HALT must NOT keep the circuit open.
A DENIED halt still holds for CIRCUIT_WINDOW_HOURS.
A human APPROVE closes the circuit immediately.
"""
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.utils import db_utils
from mcp_server.utils.purchase_flow import process_attempt
from web_api.apply_resolution import resolve_pending_action


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_utils, "DB_PATH", tmp_path / "t.db")
    db_utils.ensure_schema()


def buy(item, cat, merchant, amount, owner="u1", sess="s1"):
    d, dup = process_attempt(owner, sess, item, cat, merchant, amount)
    return d["status"], d, dup


def _expire(action_id):
    """Force expires_at into the past for a given action."""
    conn = db_utils.get_connection()
    try:
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="microseconds")
        conn.execute("UPDATE actions SET expires_at=? WHERE action_id=?", (past, action_id))
        conn.commit()
    finally:
        conn.close()


def test_expired_halt_closes_the_circuit():
    # Open a halt on merchant A.
    _, d, _ = buy("Gift card", "gift_card", "evil.com", 500)
    _expire(d["action_id"])
    # A non-Low purchase at a *different* merchant should NOT be reason="circuit open"
    # anymore. It may still halt on its own merits (unknown_merchant_over_cap, etc.),
    # but the reason must not be the circuit.
    st, d2, _ = buy("Laptop", "electronics", "anothershop", 100, sess="s2")
    assert "circuit open" not in d2["reason"]


def test_denied_halt_keeps_circuit_open_for_window():
    _, d, _ = buy("Gift card", "gift_card", "evil.com", 500)
    resolve_pending_action(d["action_id"], False, "u1")
    st, d2, _ = buy("Laptop", "electronics", "anothershop", 100, sess="s2")
    assert "circuit open" in d2["reason"]


def test_human_approve_closes_circuit_immediately():
    _, d, _ = buy("Gift card", "gift_card", "evil.com", 500)
    resolve_pending_action(d["action_id"], True, "u1")
    st, d2, _ = buy("Coffee", "subscription", "evil.com", 5, sess="s2")
    # 'evil.com' is now human-approved → trusted. Low-value → OK, no circuit.
    assert st == "OK"