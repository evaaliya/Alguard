"""
Tier 2 service: authenticated end-user channel. This is a SEPARATE process/port from
mcp_server/server.py on purpose — the Alexa+ agent has no network path to this service
at all in a real deployment (different auth, different network policy). In this repo
that separation is enforced by "the agent's MCP tool list simply does not include
resolve"; in production it should additionally be enforced at the network/auth layer.

In a real deployment, `require_authenticated_user` below is where you'd verify the
Alexa companion-app session / Amazon account token and extract the real user id —
never trust a user_id passed in the request body in production.
"""
from typing import Dict

from fastapi import FastAPI, HTTPException, Header

from web_api.apply_resolution import resolve_pending_action
from mcp_server.utils.db_utils import get_session_actions
from mcp_server.utils.receipts import get_receipt

app = FastAPI(title="Alguard Tier 2 Resolution Service")


def require_authenticated_user(x_user_id: str = Header(...)) -> str:
    # Placeholder for real Amazon account/session verification.
    if not x_user_id:
        raise HTTPException(status_code=401, detail="missing authenticated user")
    return x_user_id


@app.post("/resolve/{action_id}")
def resolve(action_id: str, approved: bool, user_id: str = Header(..., alias="X-User-Id")) -> Dict:
    resolved_by = require_authenticated_user(user_id)
    result = resolve_pending_action(action_id, approved, resolved_by)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/sessions/{session_id}")
def session_status(session_id: str) -> Dict:
    return {"session_id": session_id, "actions": get_session_actions(session_id)}


@app.get("/receipts/{action_id}")
def receipt(action_id: str) -> Dict:
    """The customer-facing view of a delivered receipt (Functional Requirements #8:
    a receipt must be deliverable via at least one channel -- this is that channel's
    read side; see mcp_server/utils/receipts.py for the write side)."""
    entry = get_receipt(action_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"No receipt found for action_id '{action_id}'")
    return entry


if __name__ == "__main__":
    import uvicorn
    # Deliberately a different port from the MCP server (8000) — different service,
    # different trust boundary.
    uvicorn.run(app, host="0.0.0.0", port=8010)