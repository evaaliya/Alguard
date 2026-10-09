"""One purchase attempt -> one decision record. All inputs are scoped to the owner."""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from mcp_server.utils.db_utils import (
    get_agent_trust, get_owner_actions, new_id, spend_since,
)
from mcp_server.utils.session_profiler import profile_purchase_attempt
from mcp_server.utils.risk_evaluator import evaluate_purchase_risk
from mcp_server.utils.circuit_breaker import compute_action_status, CIRCUIT_WINDOW_HOURS

APPROVAL_TTL_MINUTES = int(os.environ.get("ALGUARD_APPROVAL_TTL_MIN", "30"))


def build_purchase_decision(
    owner: str, session_id: str, item: str, category: str, merchant: str, amount: float
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    iso = lambda d: d.isoformat(timespec="microseconds")

    history = get_owner_actions(owner)
    profile = profile_purchase_attempt(history, item, category, merchant, amount, now)

    risk = evaluate_purchase_risk(
        category=category, amount=amount, anomaly_flags=profile["flags"],
        trust_score=get_agent_trust(owner), item=item, merchant=merchant,
        merchant_trusted=profile["merchant_trusted"],
        spend_1h=spend_since(owner, iso(now - timedelta(hours=1))),
        spend_24h=spend_since(owner, iso(now - timedelta(hours=24))),
    )

    cutoff = iso(now - timedelta(hours=CIRCUIT_WINDOW_HOURS))
    status, reason = compute_action_status(
        risk,
        [h for h in history if h["created_at"] >= cutoff],
        now=now,
    )

    return {
        "action_id": new_id("act"),
        "session_id": session_id,
        "agent_id": owner,
        "item": item,
        "category": category,
        "merchant": merchant,
        "amount": amount,
        "created_at": iso(now),
        "risk_score": risk["risk_score"],
        "category_severity": risk["category_severity"],
        "anomaly_flags": risk["anomaly_flags"],
        "anomaly_details": profile["details"],
        "status": status,
        "reason": reason,
        "expires_at": iso(now + timedelta(minutes=APPROVAL_TTL_MINUTES)) if status == "HALTED" else None,
        "resolution": None,
        "resolved_at": None,
        "resolved_by": None,
    }
