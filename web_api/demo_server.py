"""Demo-only web server for demo/mock_ui_for_video.html. Loopback only.

This surface exists ONLY to drive the demo video. It is deliberately NOT the
production approval path: identity here is a demo-only header, and the whole server
refuses to start unless explicitly opted in (ALGUARD_DEMO_UI=1).
"""
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from mcp_server.utils.db_utils import (
    ensure_schema, get_pending_for_owner, get_owner_actions, get_agent_trust, get_connection,
    list_trusted_merchants, add_trusted_merchant, remove_trusted_merchant,
    list_banned_merchants, add_banned_merchant, remove_banned_merchant,
)
from mcp_server.utils.explain import internal_explanation, customer_message
from mcp_server.utils.purchase_flow import process_attempt
from mcp_server.utils.category_risk import CATEGORY_VALUES
from web_api.apply_resolution import resolve_pending_action

logging.basicConfig(level=logging.INFO)

# Fail closed: this demo UI backend must never be reachable in production. It requires
# an explicit opt-in, and even then binds to loopback only (see __main__ below).
if not os.environ.get("ALGUARD_DEMO_UI", "").strip().lower() in ("1", "true", "yes"):
    raise SystemExit(
        "The demo UI backend refuses to start: set ALGUARD_DEMO_UI=1 to run the "
        "loopback-only demo UI. This surface is for the demo video only, never production."
    )

ensure_schema()
app = FastAPI(title="Alguard demo UI backend")
_logger = logging.getLogger("Alguard.demo-ui")
_logger.warning(
    "Demo UI backend running (ALGUARD_DEMO_UI=1). Loopback-only. The x_demo_user "
    "header is a demo identity, NOT authentication — never expose this port."
)
UI_FILE = Path(__file__).resolve().parent.parent / "demo" / "mock_ui_for_video.html"


class AttemptIn(BaseModel):
    session_id: str
    item: str
    category: str
    merchant: str
    amount: float


class TrustedIn(BaseModel):
    merchant: str


def _user(x: Optional[str]) -> str:
    return x or "demo_user"


@app.get("/")
def index():
    if not UI_FILE.exists():
        raise HTTPException(500, f"UI not found: {UI_FILE}")
    return FileResponse(UI_FILE)


@app.get("/demo/meta")
def meta():
    return {"categories": CATEGORY_VALUES}


@app.post("/demo/attempt")
def attempt(body: AttemptIn, x_demo_user: Optional[str] = Header(None)):
    user = _user(x_demo_user)
    d, dup = process_attempt(user, body.session_id, body.item, body.category, body.merchant, body.amount)
    msg = customer_message(d, duplicate=dup)
    return {
        "agent_response": {
            "action_id": d["action_id"],
            "status": d["status"],
            "duplicate": dup,
            "message": msg,
        },
        "status": d["status"],
    }


@app.get("/demo/state")
def state(x_demo_user: Optional[str] = Header(None)):
    user = _user(x_demo_user)
    h = get_owner_actions(user)
    return {
        "trust": get_agent_trust(user),
        "agent_log": list(reversed([
            {
                "action_id": a["action_id"], "item": a["item"], "amount": a["amount"],
                "merchant": a["merchant"], "status": a["status"], "resolution": a["resolution"],
                "awaiting_customer_approval": a["status"] == "HALTED" and a.get("resolution") is None,
            } for a in h
        ])),
        "pending": [
            {
                "action_id": a["action_id"], "expires_at": a["expires_at"],
                "agent_reported": {
                    "item": a["item"], "merchant": a["merchant"],
                    "amount": a["amount"], "category": a["category"],
                },
                "server_assessed": {
                    "severity": a["category_severity"],
                    "risk_score": a["risk_score"],
                    "why": internal_explanation(a),
                },
            } for a in get_pending_for_owner(user)
        ],
        "resolved": [
            {"item": a["item"], "amount": a["amount"], "resolution": a["resolution"]}
            for a in h if a.get("resolution") in ("APPROVED", "DENIED")
        ],
    }


@app.post("/demo/resolve/{action_id}")
def resolve(action_id: str, approved: bool, x_demo_user: Optional[str] = Header(None)):
    r = resolve_pending_action(action_id, approved, _user(x_demo_user))
    if "error" in r:
        raise HTTPException(409, r["error"])
    return r


@app.post("/demo/reset")
def reset(x_demo_user: Optional[str] = Header(None)):
    user = _user(x_demo_user)
    conn = get_connection()
    try:
        conn.execute("DELETE FROM actions WHERE agent_id = ?;", (user,))
        conn.execute("DELETE FROM agent_trust WHERE agent_id = ?;", (user,))
        conn.execute("DELETE FROM user_trusted_merchants WHERE owner = ?;", (user,))
        conn.execute("DELETE FROM user_banned_merchants WHERE owner = ?;", (user,))
        conn.commit()
    finally:
        conn.close()
    return {"reset": user}


# ---- trusted merchants --------------------------------------------------------------

@app.get("/demo/trusted-merchants")
def demo_trusted_list(x_demo_user: Optional[str] = Header(None)):
    return {"merchants": list_trusted_merchants(_user(x_demo_user))}


@app.post("/demo/trusted-merchants")
def demo_trusted_add(body: TrustedIn, x_demo_user: Optional[str] = Header(None)):
    m = body.merchant.strip().lower()
    if not m:
        raise HTTPException(400, "merchant must not be empty")
    add_trusted_merchant(_user(x_demo_user), m)
    return {"added": m}


@app.delete("/demo/trusted-merchants/{merchant}")
def demo_trusted_remove(merchant: str, x_demo_user: Optional[str] = Header(None)):
    remove_trusted_merchant(_user(x_demo_user), merchant)
    return {"removed": merchant.strip().lower()}


# ---- banned merchants (denylist) ----------------------------------------------------

@app.get("/demo/banned-merchants")
def demo_banned_list(x_demo_user: Optional[str] = Header(None)):
    return {"merchants": list_banned_merchants(_user(x_demo_user))}


@app.post("/demo/banned-merchants")
def demo_banned_add(body: TrustedIn, x_demo_user: Optional[str] = Header(None)):
    m = body.merchant.strip().lower()
    if not m:
        raise HTTPException(400, "merchant must not be empty")
    add_banned_merchant(_user(x_demo_user), m)
    return {"added": m}


@app.delete("/demo/banned-merchants/{merchant}")
def demo_banned_remove(merchant: str, x_demo_user: Optional[str] = Header(None)):
    remove_banned_merchant(_user(x_demo_user), merchant)
    return {"removed": merchant.strip().lower()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8020)