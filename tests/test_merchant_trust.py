"""
1.1 — merchant trust comes only from a human APPROVED resolution.
Static TRUSTED_MERCHANTS must NOT lower severity or suppress new_merchant.
New hard rule: unknown_merchant_over_cap (default $250).
Cold start: new_merchant applies even with empty history.
"""
import sys
from pathlib import Path
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


def test_static_trusted_merchant_no_longer_helps():
    # 'amazon' is in TRUSTED_MERCHANTS; without human approval it must NOT be treated as trusted.
    # $999 at 'amazon' must HALT (unknown_merchant_over_cap).
    st, _, _ = buy("Laptop", "electronics", "amazon", 999)
    assert st == "HALTED"


def test_small_purchase_at_unknown_merchant_is_monitor_not_ok():
    st, d, _ = buy("Book", "entertainment", "randomsite", 20)
    assert st == "MONITOR"
    assert "new_merchant" in d["anomaly_flags"]


def test_human_approval_makes_merchant_trusted():
    # First purchase at 'shop' is small enough to HALT? It will be MONITOR; then no approval path.
    # Use a High-severity purchase to force HALT, human approves, then the merchant is trusted.
    _, d, _ = buy("Gift card", "gift_card", "shop", 500)
    resolve_pending_action(d["action_id"], True, "u1")
    # After human approval, 'shop' is trusted: a small purchase there is OK.
    st, _, _ = buy("Coffee", "subscription", "shop", 5)
    assert st == "OK"


def test_merchant_normalisation_amazon_case_and_space():
    # 'Amazon ' and 'AMAZON' must behave identically to 'amazon'
    _, d1, _ = buy("Book", "entertainment", "amazon", 20)
    _, d2, _ = buy("Book", "entertainment", "AMAZON", 20, sess="s2")
    _, d3, _ = buy("Book", "entertainment", "Amazon ", 20, sess="s3")
    # All three should have new_merchant on cold start (empty history), no static trust.
    assert "new_merchant" in d1["anomaly_flags"]
    assert "new_merchant" in d2["anomaly_flags"]
    assert "new_merchant" in d3["anomaly_flags"]


def test_unknown_merchant_over_cap_is_hard_halt():
    st, d, _ = buy("Monitor", "electronics", "somewhere", 300)
    assert st == "HALTED"
    assert "unknown_merchant_over_cap" in d["anomaly_flags"]