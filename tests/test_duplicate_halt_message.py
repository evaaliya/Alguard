"""
1.4 — a duplicate HALTED must return the SAME approval-pending message as
the original halt, NOT "already processed".
Only OK/MONITOR duplicates may say "already processed".
"""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.utils import db_utils
from mcp_server.utils.purchase_flow import process_attempt
from mcp_server.utils.explain import customer_message
from web_api.apply_resolution import resolve_pending_action


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_utils, "DB_PATH", tmp_path / "t.db")
    db_utils.ensure_schema()


def buy(item, cat, merchant, amount, owner="u1", sess="s1"):
    return process_attempt(owner, sess, item, cat, merchant, amount)


def test_duplicate_halt_keeps_approval_message():
    d1, dup1 = buy("Gift card", "gift_card", "shop", 500)
    assert not dup1 and d1["status"] == "HALTED"
    msg1 = customer_message(d1, duplicate=False)

    d2, dup2 = buy("Gift card", "gift_card", "shop", 500, sess="s2")
    assert dup2 and d2["status"] == "HALTED"
    msg2 = customer_message(d2, duplicate=True)

    assert msg1 == msg2
    assert "approval" in msg2.lower() or "approve" in msg2.lower()
    assert "already processed" not in msg2.lower()


def test_duplicate_ok_still_says_already_processed():
    # Approve a small purchase so the merchant becomes trusted and future buys are OK.
    d1, dup1 = buy("Gift card", "gift_card", "cafe", 500, owner="u2")
    resolve_pending_action(d1["action_id"], True, "u2")
    d2, dup2 = buy("Coffee", "subscription", "cafe", 4, owner="u2", sess="s3")
    assert d2["status"] == "OK"
    # Now duplicate the OK one.
    d3, dup3 = buy("Coffee", "subscription", "cafe", 4, owner="u2", sess="s4")
    assert dup3
    msg = customer_message(d3, duplicate=True)
    assert "already processed" in msg.lower()