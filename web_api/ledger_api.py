"""Internal ledger API — the only network surface Tier 2 uses for the actions ledger.

Tier 2 (web_api/resolve_action.py) never opens the SQLite file itself; it calls this
service over HTTP. This service owns the ledger for Tier 2's needs: read-only
pending/receipts, the single atomic resolve, and the user preference lists
(trusted / banned merchants). Loopback-bound; in production it is the protected store
Tier 1 writes through.
"""
import logging
import os
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from mcp_server.utils.db_utils import (
    ensure_schema, get_pending_for_owner, reconcile_unfulfilled,
    list_trusted_merchants, add_trusted_merchant, remove_trusted_merchant,
    list_banned_merchants, add_banned_merchant, remove_banned_merchant,
)
from mcp_server.utils.receipts import get_receipt
from web_api.apply_resolution import resolve_pending_action

ensure_schema()
_logger = logging.getLogger("Alguard.ledger-api")

# The ledger API is Tier 2's ONLY route to the actions ledger. It is loopback-bound,
# and when ALGUARD_LEDGER_API_TOKEN is set it additionally requires that shared secret on
# every route — so a third party on the same host cannot read pending actions or resolve
# them directly. When unset (local dev) it runs unauthenticated on loopback only.
_LEDGER_TOKEN = os.environ.get("ALGUARD_LEDGER_API_TOKEN")
if not _LEDGER_TOKEN:
    _logger.warning(
        "ALGUARD_LEDGER_API_TOKEN is not set — the internal ledger API is unauthenticated "
        "(loopback only). Set it so only Tier 2 can call the ledger."
    )


async def require_ledger_token(x_ledger_token: Optional[str] = Header(None)) -> None:
    """Shared-secret guard. No-op in local dev (token unset); 401 otherwise."""
    if _LEDGER_TOKEN and x_ledger_token != _LEDGER_TOKEN:
        raise HTTPException(401, "ledger token required")


app = FastAPI(title="Alguard internal ledger API", dependencies=[Depends(require_ledger_token)])

# Heal APPROVED-but-unfulfilled actions left behind by a crash between resolve and
# fulfill. Idempotent; cheap on an empty/small DB; never blocks startup.
try:
    _healed = reconcile_unfulfilled()
    if _healed:
        _logger.info("Reconciled %d unfulfilled approved action(s) on startup", _healed)
except Exception as exc:
    _logger.warning("Reconciliation pass failed (will retry on next startup): %s", exc)


class MerchantIn(BaseModel):
    merchant: str


@app.get("/pending")
def pending(owner: str):
    return {"items": get_pending_for_owner(owner)}


@app.get("/receipts/{action_id}")
def receipt(action_id: str, owner: str):
    entry = get_receipt(action_id, owner)
    if not entry:
        raise HTTPException(404, "receipt not found")
    return entry


@app.post("/resolve/{action_id}")
def resolve(action_id: str, approved: bool, owner: str):
    return resolve_pending_action(action_id, approved, owner)


# ---- trusted merchants (allowlist) ---------------------------------------------------

@app.get("/trusted-merchants")
def trusted_list(owner: str):
    return {"merchants": list_trusted_merchants(owner)}


@app.post("/trusted-merchants")
def trusted_add(body: MerchantIn, owner: str):
    m = body.merchant.strip().lower()
    if not m:
        raise HTTPException(400, "merchant must not be empty")
    add_trusted_merchant(owner, m)
    return {"added": m}


@app.delete("/trusted-merchants/{merchant}")
def trusted_remove(merchant: str, owner: str):
    remove_trusted_merchant(owner, merchant)
    return {"removed": merchant.strip().lower()}


# ---- banned merchants (denylist) -----------------------------------------------------

@app.get("/banned-merchants")
def banned_list(owner: str):
    return {"merchants": list_banned_merchants(owner)}


@app.post("/banned-merchants")
def banned_add(body: MerchantIn, owner: str):
    m = body.merchant.strip().lower()
    if not m:
        raise HTTPException(400, "merchant must not be empty")
    add_banned_merchant(owner, m)
    return {"added": m}


@app.delete("/banned-merchants/{merchant}")
def banned_remove(merchant: str, owner: str):
    remove_banned_merchant(owner, merchant)
    return {"removed": merchant.strip().lower()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8015)
