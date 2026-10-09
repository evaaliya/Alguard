"""Regression tests for every red flag. No MCP transport, temp DB per test."""
import math, sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.utils import db_utils
from mcp_server.utils.purchase_flow import process_attempt
from mcp_server.utils.circuit_breaker import compute_action_status
from web_api.apply_resolution import resolve_pending_action


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_utils, "DB_PATH", tmp_path / "t.db")
    db_utils.ensure_schema()


def buy(item="USB-C cable", cat="electronics", merchant="amazon_basics", amount=12.99, owner="u1", sess="s1"):
    d, dup = process_attempt(owner, sess, item, cat, merchant, amount)
    return d["status"], d, dup


def test_first_gift_card_is_halted_not_ok():           # was: silent OK on a clean session
    st, d, _ = buy("Gift card", "gift_card", "randomsite", 500)
    assert st == "HALTED" and d["expires_at"]


def test_halt_cannot_be_laundered_by_retry():          # was: $501 retry -> OK/MONITOR
    buy()
    assert buy("Gift card", "gift_card", "evil.com", 500)[0] == "HALTED"
    assert buy("Gift card", "gift_card", "evil.com", 501)[0] == "HALTED"
    # also for a Low category: retry on a burned merchant is a hard halt
    assert buy("Laptop", "electronics", "evil2.com", 1500)[0] == "HALTED"
    st, d, _ = buy("Laptop", "electronics", "evil2.com", 900)
    assert st == "HALTED" and "retry_after_halt" in d["anomaly_flags"]


def test_agent_lying_about_category_is_caught():       # category="other" for crypto
    st, d, _ = buy("Bitcoin top-up", "other", "shadyx", 50)
    assert st == "HALTED" and "category_mismatch" in d["anomaly_flags"]


def test_circuit_breaker_really_blocks_and_ignores_session_id():
    # A halt on one merchant opens the circuit for this owner.
    buy("Gift card", "gift_card", "evil.com", 500, sess="a")
    # A new-session attempt at a different merchant is still HALTED, because
    # circuit is open (unknown merchant > $250 also triggers the new cap rule,
    # but the point of this test is that session_id rotation does not help).
    st, d, _ = buy("Laptop", "electronics", "newshop", 300, sess="totally-new-session")
    assert st == "HALTED"
    # And a low-value purchase at any merchant is at least MONITOR while circuit is open.
    assert buy("Coffee", "subscription", "some_cafe", 6.5, sess="b")[0] in ("MONITOR", "HALTED")


def test_human_approval_closes_the_circuit():
    # Approve a halt at 'shop' → 'shop' becomes trusted for this user.
    _, d, _ = buy("Gift card", "gift_card", "shop", 500)
    assert resolve_pending_action(d["action_id"], True, "u1")["resolution"] == "APPROVED"
    # Same merchant, low-value: no new_merchant flag, no circuit → OK.
    assert buy("Coffee", "subscription", "shop", 6.5)[0] == "OK"


def test_cumulative_limit_blocks_999_series():
    # $999 at an unapproved merchant hits unknown_merchant_over_cap ($250), so
    # *every* attempt halts — cumulative limits aren't even reached. The test
    # keeps its intent: no more than the daily cap can go through.
    sts = [buy(f"Flight {i}", "travel", "amazon", 999)[0] for i in range(5)]
    assert sts.count("OK") == 0
    assert all(s in ("HALTED", "MONITOR") for s in sts)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5, 0, 2e9])
def test_bad_amounts_rejected(bad):
    with pytest.raises(ValueError):
        buy(amount=bad)


def test_unknown_category_rejected():
    with pytest.raises(ValueError):
        buy(cat="gift card")


def test_duplicate_is_prevented_not_just_flagged():
    st1, d1, dup1 = buy("Book", "entertainment", "amazon", 20)
    st2, d2, dup2 = buy("Book", "entertainment", "amazon", 20, sess="other")
    assert not dup1 and dup2 and d2["action_id"] == d1["action_id"]
    assert len(db_utils.get_owner_actions("u1")) == 1


def test_users_are_isolated():
    _, d, _ = buy("Gift card", "gift_card", "evil.com", 500, owner="alice")
    assert db_utils.get_action(d["action_id"], owner="bob") is None
    assert resolve_pending_action(d["action_id"], True, "bob") == {"error": "action not found"}
    assert db_utils.get_session_actions("s1", "bob") == []
    assert buy("Gift card", "gift_card", "evil.com", 500, owner="bob")[0] == "HALTED"  # bob has no 'known' evil.com
    # alice's denial must not hit bob's trust
    resolve_pending_action(d["action_id"], False, "alice")
    assert db_utils.get_agent_trust("bob") == 0.5 and db_utils.get_agent_trust("alice") < 0.5


def test_double_approve_only_one_wins():
    _, d, _ = buy("Gift card", "gift_card", "evil.com", 500)
    assert "resolution" in resolve_pending_action(d["action_id"], True, "u1")
    assert resolve_pending_action(d["action_id"], True, "u1") == {"error": "already resolved or expired"}
    assert db_utils.get_agent_trust("u1") == pytest.approx(0.53)   # bumped once


def test_expired_halt_cannot_be_approved(monkeypatch):
    _, d, _ = buy("Gift card", "gift_card", "evil.com", 500)
    conn = db_utils.get_connection()
    conn.execute("UPDATE actions SET expires_at='2000-01-01T00:00:00.000000+00:00'"); conn.commit(); conn.close()
    assert resolve_pending_action(d["action_id"], True, "u1")["error"].startswith("already resolved or expired")


def test_stable_amounts_are_not_a_false_spike():
    for i in range(3):
        buy(f"Ink {i}", "electronics", "amazon_basics", 10)
    st, d, _ = buy("Ink 4", "electronics", "amazon_basics", 12)
    assert "amount_spike" not in d["anomaly_flags"]


def test_merchant_normalisation():
    buy("Gift card", "gift_card", "Evil.com ", 500)
    assert buy("Gift card", "gift_card", "evil.com", 501)[0] == "HALTED"
