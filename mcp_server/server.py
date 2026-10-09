"""
Tier 1 (machine-to-machine) MCP server -- this is what Alexa+ connects to as
the MCP client (Alexa+ MCP Toolkit Overview: "Alexa+ acts as the MCP client.
You return standard MCP responses, and Alexa+ handles natural language
understanding, response generation, and UI rendering.").

Hard constraint (do not weaken this without re-reading the design doc):
  This server exposes attempt_purchase / get_session_status / get_audit_trail
  ONLY. It intentionally does NOT expose a "resolve_pending_action" or
  "approve_purchase" tool. Human approval of a HALTED action lives exclusively
  in web_api/, a separate service reachable only through an authenticated
  end-user channel (Tier 2, the Alexa app / account-linked identity -- never
  the agent's MCP channel). If you ever find yourself tempted to add an
  approval tool here "for convenience", stop -- that would let the agent
  approve its own halted purchases, which defeats the whole point of this
  layer.

Alexa+ MCP Toolkit requirements this file follows (developer.amazon.com,
MCP Design Guide for Alexa+ + MCP QuickStart Guide):
  - Streamable HTTP, MCP spec 2025-11-25+ (technical requirement).
  - Each tool represents ONE customer intent; description states when to
    call it, why, and what it returns ("Tools, Schema, and Data Design").
  - "Declare only what you honor": every parameter Alexa can fill must be
    one this server actually uses. Caller identity is NOT such a parameter
    (see mcp_server/utils/auth_context.py) -- it comes from the authenticated
    request context, never from an LLM-suppliable argument.
  - "Always return something": every tool below returns a dict on every
    path, including errors -- a tool that returns nothing gives Alexa+
    nothing to build a spoken/visual response from.
  - Payload hygiene: no third-party tracking params or upstream deep links
    in structuredContent -- nothing here does that.

NOT yet implemented (explicit scope boundary, not an oversight):
  - Account Linking / OAuth 2.1 + PKCE(S256), the RFC 9728 Protected Resource
    Metadata document, and 401-without-WWW-Authenticate handling from the
    MCP Toolkit Authentication checklist. Until this is wired up in the
    Amazon developer console, resolve_agent_identity() returns a clearly
    logged placeholder -- see auth_context.py.
"""
import json
import logging
import os
from typing import Any, Dict, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings
from pydantic import AnyHttpUrl

from mcp_server.utils.db_utils import ensure_schema, get_session_actions, get_action
from mcp_server.utils.explain import customer_message
from mcp_server.utils.auth_context import build_token_verifier_from_env, resolve_agent_identity
from mcp_server.utils.purchase_flow import process_attempt
from mcp_server.utils.category_risk import CATEGORY_VALUES
from dotenv import load_dotenv
load_dotenv()   # loads alguard/.env if present; harmless if missing

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Alguard-MCP")

ensure_schema()

# OAuth 2.1 resource-server auth (see auth_context.py) turns on ONLY when OAUTH_*
# env vars are set. Without them this runs with no auth at all -- fine for local
# dev against demo/simulate_session.py, not acceptable for certification.
_token_verifier = build_token_verifier_from_env()
_DEV_MODE = os.environ.get("ALGUARD_DEV_MODE", "").strip().lower() in ("1", "true", "yes")

if _token_verifier is not None:
    mcp = FastMCP(
        "Alguard",
        token_verifier=_token_verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(os.environ["OAUTH_ISSUER_URL"]),
            resource_server_url=AnyHttpUrl(
                os.environ.get("OAUTH_RESOURCE_SERVER_URL", "http://localhost:8000/mcp")
            ),
            required_scopes=[s for s in os.environ.get("OAUTH_REQUIRED_SCOPES", "").split() if s] or None,
        ),
    )
    logger.info("OAuth resource-server auth ENABLED (issuer=%s)", os.environ["OAUTH_ISSUER_URL"])
elif _DEV_MODE:
    mcp = FastMCP("Alguard")
    logger.warning("ALGUARD_DEV_MODE=1 -- running with NO auth. Local development only, never for certification.")
else:
    # Fail closed: never silently start an unauthenticated purchase-halt endpoint.
    raise SystemExit(
        "Alguard refuses to start without authentication. "
        "Set OAUTH_JWKS_URL, OAUTH_ISSUER_URL and OAUTH_AUDIENCE to enable OAuth 2.1 "
        "resource-server auth, or set ALGUARD_DEV_MODE=1 to explicitly run "
        "unauthenticated for local development only."
    )

# Declared as a Literal so it's an enum in the tool's JSON Schema, per Functional
# Requirements #13: "Include common synonyms... in your tool parameter descriptions
# and enums so variants resolve correctly." A closed set also means category_risk.py
# does exact matching instead of guessing at substrings the model might phrase
# differently ("gift card" vs "gift_card").
CategoryLiteral = Literal[tuple(CATEGORY_VALUES)]  # type: ignore[valid-type]


@mcp.tool()
def attempt_purchase(
    session_id: str, item: str, category: CategoryLiteral, merchant: str, amount: float
) -> Dict[str, Any]:
    """
    Call this when the customer asks Alexa+ to buy, book, or subscribe to
    something on their behalf. Do not call it just to check a price or
    browse options -- only when the customer intends to complete a purchase.

    Why it's used: every purchase attempt is risk-scored before anything is
    charged -- never assume the purchase completed just because this tool was
    called.

    What it returns: `recorded` is true only when a purchase was recorded; `status` is one of "OK" (completed, no concerns),
    "MONITOR" (completed, flagged), or "HALTED" (NOT
    completed -- tell the customer it needs their approval in the Alexa app;
    Alexa+ itself cannot approve it). `message` is ready to read back to
    the customer as-is. Nothing is charged by this server yet (simulated).

    `category`: pick the closest match from the enum -- if the item doesn't
    fit any of the specific values, use "other" rather than inventing a new one.

    `session_id`: reuse the same value for every purchase attempt within one
    ongoing shopping conversation (add to cart -> apply coupon -> confirm),
    so a risky earlier step in the same conversation is correctly reflected
    in later ones. Start a new value for a new, unrelated shopping request.
    """
    owner = resolve_agent_identity()          # verified end-user `sub`, never a tool argument
    decision, duplicate = process_attempt(owner, session_id, item, category, merchant, amount)

    logger.info("action=%s status=%s owner=%s dup=%s amount=%s",
                decision["action_id"], decision["status"], owner, duplicate, decision["amount"])

    # Deliberately minimal: no risk_score, flags, thresholds or reason go back to the agent
    # (they would be an oracle for a hostile agent). Full detail lives on the Tier 2 screen.
    return {
        "action_id": decision["action_id"],
        "status": decision["status"],
        "recorded": decision["status"] in ("OK", "MONITOR") and not duplicate,
        "duplicate": duplicate,
        "message": customer_message(decision, duplicate),
    }


def _agent_view(a: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "action_id": a["action_id"], "status": a["status"], "item": a["item"],
        "merchant": a["merchant"], "amount": a["amount"], "created_at": a["created_at"],
        "awaiting_customer_approval": a["status"] == "HALTED" and a.get("resolution") is None,
        "resolution": a.get("resolution"),
        "fulfillment": a.get("fulfillment"),   # NULL | COMPLETED_SIMULATED | FAILED
    }


@mcp.tool()
def get_session_status(session_id: str) -> Dict[str, Any]:
    """
    Call this when the customer asks about the status of a purchase they
    just tried to make in this conversation, or asks "did that go through".
    Do not call it to look up unrelated past purchases outside this session.

    Returns the chronological list of purchase attempts (and their
    OK/MONITOR/HALTED outcomes) for this session, oldest first.
    """
    actions = [_agent_view(a) for a in get_session_actions(session_id, resolve_agent_identity())]
    if not actions:
        return {"session_id": session_id, "actions": [], "message": "No purchase attempts found for this session."}
    return {"session_id": session_id, "actions": actions}


@mcp.tool()
def get_audit_trail(action_id: str) -> Dict[str, Any]:
    """
    Call this when the customer asks for details on a specific past purchase
    attempt they already have the action_id for (typically returned by
    attempt_purchase or get_session_status), e.g. "why was that halted".

    Returns the status record for one action, including its Tier 2 resolution
    (approved/denied) if a human has already resolved it. Read-only -- this
    tool cannot approve or deny anything; it only reports what happened.
    """
    action = get_action(action_id, owner=resolve_agent_identity())  # foreign == missing
    if not action:
        raise ValueError(f"No purchase attempt found with action_id '{action_id}'")
    return _agent_view(action)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")