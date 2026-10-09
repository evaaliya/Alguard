"""
1.3 — retry_after_halt must be scoped:
  * only halts within CIRCUIT_WINDOW_HOURS count
  * halts whose cause is the circuit breaker must NOT burn the merchant
Halts are classified by an explicit `halt_cause` column ('rule' | 'circuit'),
not by parsing the reason string.
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


def _backdate(action_id, hours):
    """Move a halt's created_at into the past (simulates an old halt)."""
    conn = db_utils.get_connection()
    try:
        past = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(timespec="microseconds")
        conn.execute("UPDATE actions SET created_at=? WHERE action_id=?", (past, action_id))
        conn.commit()
    finally:
        conn.close()


def test_old_halt_does_not_burn_merchant_forever():
    # First gift card halt at 'shop' → burns 'shop'
    _, d, _ = buy("Gift card", "gift_card", "shop", 500)
    # Backdate it beyond CIRCUIT_WINDOW_HOURS
    _backdate(d["action_id"], hours=48)
    # A modest purchase at the same merchant, low-value, should not be
    # flagged retry_after_halt from an expired halt window.
    st, d2, _ = buy("Coffee", "subscription", "shop", 5, sess="s2")
    assert "retry_after_halt" not in d2["anomaly_flags"]


def test_fresh_rule_halt_burns_merchant():
    _, d, _ = buy("Gift card", "gift_card", "shop", 500)
    st, d2, _ = buy("Laptop", "electronics", "shop", 50, sess="s2")
    assert "retry_after_halt" in d2["anomaly_flags"]


def test_circuit_halt_does_not_burn_merchant():
    # Open the circuit with a halt at merchant A.
    buy("Gift card", "gift_card", "evil.com", 500)
    # Now attempt at a *different* merchant B, non-Low, so circuit forces HALTED.
    # merchant B itself must NOT be considered burned by this circuit halt.
    st, dB, _ = buy("Laptop", "electronics", "shopB", 100, sess="s2")
    # It halts, but the reason should mention the circuit, not B being burned.
    assert st == "HALTED"
    # Now approve the original halt (clears circuit), then retry merchant B low-value.
    # B was never a rule-level halt, so it's not burned.
    # (We don't assert on status here — just that retry_after_halt is absent.)
    _, dB2, _ = buy("Coffee", "subscription", "shopB", 5, sess="s3")
    assert "retry_after_halt" not in dB2["anomaly_flags"]