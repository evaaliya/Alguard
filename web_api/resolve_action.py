"""
Tier 2: the human channel. Separate process, separate port, bound to loopback (put a real
reverse proxy / the Alexa app backend in front of it).

Authentication FAILS CLOSED:
  - OAuth configured (OAUTH_TIER2_AUDIENCE etc.)  -> Bearer JWT, verified, user = `sub`.
  - not configured -> only if ALGUARD_DEV_USER is set (local demo), loudly warned.
  - neither -> 503. There is no header anyone can type to become "the user".
"""
import logging
import os
from typing import Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException

from mcp_server.utils.auth_context import build_tier2_verifier_from_env
from mcp_server.utils.db_utils import ensure_schema, get_pending_for_owner
from mcp_server.utils.explain import internal_explanation
from mcp_server.utils.receipts import get_receipt
from web_api.apply_resolution import resolve_pending_action
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger("Alguard.tier2")
ensure_schema()
app = FastAPI(title="Alguard Tier 2 Resolution Service")

_verifier = build_tier2_verifier_from_env()
_DEV_USER = None if _verifier else os.environ.get("ALGUARD_DEV_USER")
if _verifier is None:
    logger.warning("Tier 2 OAuth NOT configured; dev user = %r. Not for production.", _DEV_USER)


async def current_user(authorization: Optional[str] = Header(None)) -> str:
    if _verifier is not None:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, "bearer token required", headers={"WWW-Authenticate": "Bearer"})
        tok = await _verifier.verify_token(authorization[7:].strip())
        if tok is None:
            raise HTTPException(401, "invalid token", headers={"WWW-Authenticate": "Bearer"})
        return tok.client_id  # = `sub`
    if _DEV_USER:
        return _DEV_USER
    raise HTTPException(503, "Tier 2 authentication is not configured")


from fastapi import Depends  # noqa: E402


@app.get("/pending")
async def pending(user: str = Depends(current_user)) -> Dict:
    """The user's own open approvals -- this is how the app learns action_ids."""
    items: List[Dict] = []
    for a in get_pending_for_owner(user):
        items.append({
            "action_id": a["action_id"],
            "expires_at": a["expires_at"],
            # reported BY THE AGENT -- show with that caveat in the UI
            "agent_reported": {"item": a["item"], "merchant": a["merchant"],
                               "amount": a["amount"], "category": a["category"]},
            "server_assessed": {"severity": a["category_severity"], "why": internal_explanation(a)},
        })
    return {"pending": items}


@app.post("/resolve/{action_id}")
async def resolve(action_id: str, approved: bool, user: str = Depends(current_user)) -> Dict:
    result = resolve_pending_action(action_id, approved, user)
    if "error" in result:
        code = 404 if result["error"] == "action not found" else 409
        raise HTTPException(code, result["error"])
    return result


@app.get("/receipts/{action_id}")
async def receipt(action_id: str, user: str = Depends(current_user)) -> Dict:
    entry = get_receipt(action_id, owner=user)
    if not entry:
        raise HTTPException(404, "receipt not found")
    return entry


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8010)
