"""User-controlled denylist: purchases from banned merchants always HALT."""
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


def test_banned_merchant_always_halts():
    db_utils.add_banned_merchant("u1", "scam-shop")
    d, _ = buy("Book", "entertainment", "scam-shop", 20)
    assert d["status"] == "HALTED"
    assert "banned_merchant" in d["anomaly_flags"]
    assert "banned_merchant" in d["reason"]


def test_banned_merchant_not_overridden_by_trust():
    # Even allowlisting it does not lift the ban.
    db_utils.add_trusted_merchant("u1", "scam-shop")
    db_utils.add_banned_merchant("u1", "scam-shop")
    d, _ = buy("Book", "entertainment", "scam-shop", 20)
    assert d["status"] == "HALTED"
    assert "banned_merchant" in d["anomaly_flags"]


def test_removed_ban_reverts_to_normal():
    db_utils.add_banned_merchant("u1", "corner-cafe")
    db_utils.remove_banned_merchant("u1", "corner-cafe")
    d, _ = buy("Coffee", "subscription", "corner-cafe", 4.50)
    assert "banned_merchant" not in d["anomaly_flags"]


def test_ban_list_is_per_owner():
    db_utils.add_banned_merchant("u1", "scam-shop")
    assert db_utils.list_banned_merchants("u1") == ["scam-shop"]
    assert db_utils.list_banned_merchants("u2") == []
    d, _ = buy("Book", "entertainment", "scam-shop", 20, owner="u2")
    assert "banned_merchant" not in d["anomaly_flags"]