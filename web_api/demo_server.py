"""Demo-only web server for demo/mock_ui_for_video.html. Loopback only."""
import logging
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from mcp_server.utils.db_utils import ensure_schema, get_pending_for_owner, get_owner_actions, get_agent_trust, get_connection
from mcp_server.utils.explain import internal_explanation
from mcp_server.utils.purchase_flow import process_attempt
from mcp_server.utils.category_risk import CATEGORY_VALUES
from web_api.apply_resolution import resolve_pending_action

logging.basicConfig(level=logging.INFO)
ensure_schema()
app = FastAPI(title="Alguard demo UI backend")
UI_FILE = Path(__file__).resolve().parent.parent / "demo" / "mock_ui_for_video.html"

class AttemptIn(BaseModel):
    session_id: str; item: str; category: str; merchant: str; amount: float

def _user(x: Optional[str]) -> str: return x or "demo_user"

@app.get("/")
def index():
    if not UI_FILE.exists(): raise HTTPException(500, f"UI not found: {UI_FILE}")
    return FileResponse(UI_FILE)

@app.get("/demo/meta")
def meta(): return {"categories": CATEGORY_VALUES}

@app.post("/demo/attempt")
def attempt(body: AttemptIn, x_demo_user: Optional[str] = Header(None)):
    user = _user(x_demo_user)
    d, dup = process_attempt(user, body.session_id, body.item, body.category, body.merchant, body.amount)
    msg = (f"'{d['item']}' (${d['amount']:,.2f}) has NOT been made. It needs the customer's own approval in the Alexa app (reference {d['action_id']}). It cannot be approved through the assistant."
           if d["status"] == "HALTED" and not dup
           else ("This exact request was already processed a moment ago." if dup
                 else f"'{d['item']}' (${d['amount']:,.2f}) has been recorded."))
    return {"agent_response": {"action_id": d["action_id"], "status": d["status"], "duplicate": dup, "message": msg}, "status": d["status"]}

@app.get("/demo/state")
def state(x_demo_user: Optional[str] = Header(None)):
    user = _user(x_demo_user)
    h = get_owner_actions(user)
    return {
        "trust": get_agent_trust(user),
        "agent_log": list(reversed([{"action_id": a["action_id"], "item": a["item"], "amount": a["amount"], "merchant": a["merchant"], "status": a["status"], "resolution": a["resolution"], "awaiting_customer_approval": a["status"] == "HALTED" and a.get("resolution") is None} for a in h])),
        "pending": [{"action_id": a["action_id"], "expires_at": a["expires_at"], "agent_reported": {"item": a["item"], "merchant": a["merchant"], "amount": a["amount"], "category": a["category"]}, "server_assessed": {"severity": a["category_severity"], "risk_score": a["risk_score"], "why": internal_explanation(a)}} for a in get_pending_for_owner(user)],
        "resolved": [{"item": a["item"], "amount": a["amount"], "resolution": a["resolution"]} for a in h if a.get("resolution") in ("APPROVED", "DENIED")],
    }

@app.post("/demo/resolve/{action_id}")
def resolve(action_id: str, approved: bool, x_demo_user: Optional[str] = Header(None)):
    r = resolve_pending_action(action_id, approved, _user(x_demo_user))
    if "error" in r: raise HTTPException(409, r["error"])
    return r

@app.post("/demo/reset")
def reset(x_demo_user: Optional[str] = Header(None)):
    user = _user(x_demo_user)
    conn = get_connection()
    try:
        conn.execute("DELETE FROM actions WHERE agent_id = ?;", (user,))
        conn.execute("DELETE FROM agent_trust WHERE agent_id = ?;", (user,))
        conn.commit()
    finally: conn.close()
    return {"reset": user}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8020)
