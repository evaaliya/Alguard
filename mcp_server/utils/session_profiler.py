"""
Structural port of Albugent's anomaly_profiler.py.
There: SQL profiling of one table for null-rates / negative values / date inversions.
Here: profiling one agent's action history for signals that a single purchase attempt
looks anomalous relative to that agent's own track record — no ML, just the same kind
of deterministic, explainable checks.
"""
from typing import Any, Dict, List
from statistics import mean, pstdev

from mcp_server.utils.db_utils import get_agent_history
from mcp_server.utils.risk_evaluator import ABSOLUTE_AMOUNT_LIMIT

NEW_MERCHANT_FLAG = "new_merchant"
AMOUNT_SPIKE_FLAG = "amount_spike"
FREQUENCY_SPIKE_FLAG = "frequency_spike"
DUPLICATE_ATTEMPT_FLAG = "duplicate_attempt"
AMOUNT_OVER_LIMIT_FLAG = "amount_over_limit"

# Minimum history before we trust a mean/stdev comparison at all — avoids flagging
# every single action for a brand-new agent as "anomalous" purely for lack of data.
MIN_HISTORY_FOR_AMOUNT_CHECK = 3
AMOUNT_ZSCORE_THRESHOLD = 2.0
FREQUENCY_WINDOW_ACTIONS = 5  # look at the last N actions across any category


def profile_purchase_attempt(
    agent_id: str, session_id: str, item: str, category: str, merchant: str, amount: float
) -> Dict[str, Any]:
    """
    Returns a summary shaped like anomaly_profiler's output: named anomaly buckets,
    each one independently explainable to a human ("why was this flagged").
    """
    history = get_agent_history(agent_id)
    category_history = [h for h in history if h.get("category") == category]

    summary: Dict[str, Any] = {
        "agent_id": agent_id,
        "prior_actions_count": len(history),
        "flags": [],
        "details": {},
    }

    # Hard-limit flag: recorded so the audit trail and the customer-facing explanation
    # show WHY it was halted. The halt itself is enforced in risk_evaluator.py.
    if amount is not None and amount > ABSOLUTE_AMOUNT_LIMIT:
        summary["flags"].append(AMOUNT_OVER_LIMIT_FLAG)
        summary["details"][AMOUNT_OVER_LIMIT_FLAG] = {
            "amount": amount, "limit": ABSOLUTE_AMOUNT_LIMIT,
        }

    # 0. Duplicate-attempt check -- Alexa+ Functional Requirements, Transaction Flow:
    # "Detect and prevent duplicate transactions." Scoped to THIS session: the same
    # item/merchant/amount attempted twice in one shopping conversation is very
    # likely a retry/double-tap, not two separate purchases.
    session_actions = [h for h in history if h.get("session_id") == session_id]
    is_duplicate = any(
        h.get("item") == item and h.get("merchant") == merchant and h.get("amount") == amount
        for h in session_actions
    )
    if is_duplicate:
        summary["flags"].append(DUPLICATE_ATTEMPT_FLAG)
        summary["details"][DUPLICATE_ATTEMPT_FLAG] = {
            "item": item, "merchant": merchant, "amount": amount, "session_id": session_id,
        }

    # 1. New-merchant check — analog of Albugent's "orphan" node (no prior lineage).
    # Skipped on an agent's very first-ever action: with zero history, EVERY merchant
    # is "new" and the flag carries no signal — same reasoning as Albugent excluding
    # root nodes from is_orphan.
    known_merchants = {h["merchant"] for h in history if h.get("merchant")}
    if history and merchant and merchant not in known_merchants:
        summary["flags"].append(NEW_MERCHANT_FLAG)
        summary["details"][NEW_MERCHANT_FLAG] = {
            "merchant": merchant,
            "known_merchants_count": len(known_merchants),
        }

    # 2. Amount anomaly — analog of the numeric_anomalies check (negative / out-of-range).
    # Here: is this amount a statistical outlier vs this agent's own history in this category.
    amounts = [h["amount"] for h in category_history if h.get("amount") is not None]
    if len(amounts) >= MIN_HISTORY_FOR_AMOUNT_CHECK:
        hist_mean = mean(amounts)
        hist_stdev = pstdev(amounts) or 1.0  # avoid div-by-zero on identical historical amounts
        z_score = (amount - hist_mean) / hist_stdev
        if z_score >= AMOUNT_ZSCORE_THRESHOLD:
            summary["flags"].append(AMOUNT_SPIKE_FLAG)
            summary["details"][AMOUNT_SPIKE_FLAG] = {
                "amount": amount,
                "historical_mean": round(hist_mean, 2),
                "z_score": round(z_score, 2),
            }

    # 3. Frequency spike — analog of a date-logic anomaly: too many actions too fast.
    if len(history) >= FREQUENCY_WINDOW_ACTIONS:
        recent = history[-FREQUENCY_WINDOW_ACTIONS:]
        recent_ts = [h["created_at"] for h in recent]
        # Cheap heuristic without pulling in a full time-series lib: if the last
        # FREQUENCY_WINDOW_ACTIONS actions all fall inside the same session, that's
        # the spike signal we actually care about for a live agent session.
        recent_sessions = {h["session_id"] for h in recent}
        if len(recent_sessions) == 1:
            summary["flags"].append(FREQUENCY_SPIKE_FLAG)
            summary["details"][FREQUENCY_SPIKE_FLAG] = {
                "window": FREQUENCY_WINDOW_ACTIONS,
                "session_id": recent[0]["session_id"],
                "timestamps": recent_ts,
            }

    return summary