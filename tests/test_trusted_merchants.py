"""User-controlled allowlist of merchants: added merchants auto-approve, removed ones revert."""
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.utils import db_utils
from mcp_server.utils.purchase_flow import process_attempt


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_utils, "DB_PATH", tmp_path / "t.db")
    db_utils.ensure_schema()


def buy(item, cat, merchant, amount, owner="u1", sess="s1"):
    return process_attempt(owner, sess, item, cat, merchant, amount)


def test_trusted_merchant_skips_new_merchant_flag():
    db_utils.add_trusted_merchant("u1", "corner-cafe")
    d, _ = buy("Coffee", "subscription", "corner-cafe", 4.50)
    assert d["status"] == "OK"
    assert "new_merchant" not in d["anomaly_flags"]


def test_trusted_merchant_skips_unknown_cap():
    db_utils.add_trusted_merchant("u1", "bigshop")
    d, _ = buy("Laptop", "electronics", "bigshop", 500)
    # Over $250, but trusted → no unknown_merchant_over_cap; Low severity → MONITOR at worst
    assert "unknown_merchant_over_cap" not in d["anomaly_flags"]
    assert d["status"] in ("OK", "MONITOR")


def test_removed_merchant_reverts_to_unknown():
    db_utils.add_trusted_merchant("u1", "corner-cafe")
    db_utils.remove_trusted_merchant("u1", "corner-cafe")
    d, _ = buy("Coffee", "subscription", "corner-cafe", 4.50)
    assert "new_merchant" in d["anomaly_flags"]


def test_trusted_list_is_per_owner():
    db_utils.add_trusted_merchant("u1", "corner-cafe")
    assert db_utils.list_trusted_merchants("u1") == ["corner-cafe"]
    assert db_utils.list_trusted_merchants("u2") == []
    d, _ = buy("Coffee", "subscription", "corner-cafe", 4.50, owner="u2")
    assert "new_merchant" in d["anomaly_flags"]


def test_high_risk_still_halts_even_if_trusted():
    # Trusted merchant does NOT bypass high-risk categories.
    db_utils.add_trusted_merchant("u1", "shop")
    d, _ = buy("Gift card", "gift_card", "shop", 500)
    assert d["status"] == "HALTED"


def test_allowlist_clears_retry_after_halt():
    # A halt "burns" a merchant; allowlisting it is a human trust signal that
    # clears the burn so a cheaper purchase no longer trips retry_after_halt.
    d1, _ = buy("Laptop", "electronics", "shop", 300)
    assert d1["status"] == "HALTED"
    assert "unknown_merchant_over_cap" in d1["anomaly_flags"]

    db_utils.add_trusted_merchant("u1", "shop")

    d2, _ = buy("Keyboard", "electronics", "shop", 20, sess="s2")
    assert "retry_after_halt" not in d2["anomaly_flags"]
    assert d2["status"] in ("OK", "MONITOR")