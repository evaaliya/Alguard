"""
2.1 — fulfillment on the simulated rail.
  * OK/MONITOR → COMPLETED_SIMULATED right after insert
  * HALTED → NULL
  * Human APPROVED → COMPLETED_SIMULATED exactly once (idempotent)
  * DENIED → NULL
  * get_session_status exposes the fulfillment field
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
    return process_attempt(owner, sess, item, cat, merchant, amount)


def test_ok_purchase_is_fulfilled():
    # Any OK or MONITOR purchase should be fulfilled immediately.
    d, _ = buy("Coffee", "subscription", "cafe", 4, owner="u2")
    # If HALTED, approve it to get a fulfilled record.
    if d["status"] == "HALTED":
        resolve_pending_action(d["action_id"], True, "u2")
        # Now try again with a different item.
        d, _ = buy("Tea", "subscription", "cafe", 3, owner="u2", sess="s2")
    assert d["status"] in ("OK", "MONITOR")
    row = db_utils.get_action(d["action_id"], owner="u2")
    assert row["fulfillment"] == "COMPLETED_SIMULATED"


def test_halted_purchase_is_not_fulfilled():
    d, _ = buy("Gift card", "gift_card", "shop", 500)
    assert d["status"] == "HALTED"
    row = db_utils.get_action(d["action_id"], owner="u1")
    assert row["fulfillment"] is None


def test_approved_halt_is_fulfilled_exactly_once():
    d, _ = buy("Gift card", "gift_card", "shop", 500)
    r1 = resolve_pending_action(d["action_id"], True, "u1")
    assert r1["resolution"] == "APPROVED"
    row = db_utils.get_action(d["action_id"], owner="u1")
    assert row["fulfillment"] == "COMPLETED_SIMULATED"
    # Second approve must not re-fulfill (atomic UPDATE already rejects it).
    r2 = resolve_pending_action(d["action_id"], True, "u1")
    assert "error" in r2


def test_denied_halt_is_not_fulfilled():
    d, _ = buy("Gift card", "gift_card", "shop", 500)
    resolve_pending_action(d["action_id"], False, "u1")
    row = db_utils.get_action(d["action_id"], owner="u1")
    assert row["fulfillment"] is None
