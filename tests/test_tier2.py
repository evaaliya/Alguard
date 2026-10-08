import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import importlib, pytest
from fastapi.testclient import TestClient
from mcp_server.utils import db_utils
from mcp_server.utils.purchase_flow import process_attempt


def load_app(monkeypatch, tmp_path, dev_user):
    monkeypatch.setattr(db_utils, "DB_PATH", tmp_path / "t.db")
    for k in ("OAUTH_JWKS_URL", "OAUTH_ISSUER_URL", "OAUTH_TIER2_AUDIENCE"):
        monkeypatch.delenv(k, raising=False)
    if dev_user: monkeypatch.setenv("ALGUARD_DEV_USER", dev_user)
    else: monkeypatch.delenv("ALGUARD_DEV_USER", raising=False)
    import web_api.resolve_action as ra
    importlib.reload(ra)
    return TestClient(ra.app)


def test_fails_closed_without_auth_config(monkeypatch, tmp_path):
    c = load_app(monkeypatch, tmp_path, None)
    assert c.get("/pending").status_code == 503
    # the old attack: a self-declared header must NOT work
    assert c.post("/resolve/act_x?approved=true", headers={"X-User-Id": "anyone"}).status_code == 503


def test_owner_flow_and_isolation(monkeypatch, tmp_path):
    c = load_app(monkeypatch, tmp_path, "alice")
    d, _ = process_attempt("alice", "s", "Gift card", "gift_card", "evil.com", 500)
    p = c.get("/pending").json()["pending"]
    assert len(p) == 1 and p[0]["action_id"] == d["action_id"]
    # a different user cannot see or resolve it
    d2, _ = process_attempt("bob", "s", "Gift card", "gift_card", "evil.com", 500)
    assert c.post(f"/resolve/{d2['action_id']}?approved=true").status_code == 404
    assert c.post(f"/resolve/{d['action_id']}?approved=true").status_code == 200
    assert c.post(f"/resolve/{d['action_id']}?approved=true").status_code == 409
    assert c.get(f"/receipts/{d2['action_id']}").status_code == 404
