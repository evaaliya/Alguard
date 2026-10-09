"""
Deterministic, explainable anomaly checks over ONE OWNER's history (pure function: the
caller passes the history in, which makes it unit-testable without a database).

Fixes vs the old version:
  - the baseline is only what actually happened or was approved by the human
    (OK/MONITOR/APPROVED). HALTED-and-never-approved attempts do NOT make a merchant,
    amount or category "normal" -> no laundering a halt by retrying;
  - a merchant that was halted and never approved raises `retry_after_halt` (hard rule);
  - frequency is time-based (attempts in the last 10 min), not "same session_id" -- so
    rotating session_id no longer hides it;
  - amount_spike uses a stdev floor (25% of the mean) so 10,10,10 -> 12 is not a spike.
"""
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional

from mcp_server.utils.risk_evaluator import ABSOLUTE_AMOUNT_LIMIT, UNKNOWN_MERCHANT_CAP

NEW_MERCHANT_FLAG = "new_merchant"
AMOUNT_SPIKE_FLAG = "amount_spike"
FREQUENCY_SPIKE_FLAG = "frequency_spike"
AMOUNT_OVER_LIMIT_FLAG = "amount_over_limit"
RETRY_AFTER_HALT_FLAG = "retry_after_halt"
UNKNOWN_MERCHANT_OVER_CAP_FLAG = "unknown_merchant_over_cap"

MIN_HISTORY_FOR_AMOUNT_CHECK = 3
AMOUNT_ZSCORE_THRESHOLD = 2.0
FREQUENCY_WINDOW_MINUTES = 10
FREQUENCY_MAX_ATTEMPTS = 5   # this many prior attempts inside the window -> spike


def _real(h: Dict[str, Any]) -> bool:
    """Did this action actually happen, or was it approved by a human?"""
    return h.get("status") in ("OK", "MONITOR") or h.get("resolution") == "APPROVED"


def _ts(h: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(h["created_at"])
    except Exception:
        return None


def profile_purchase_attempt(
    history: List[Dict[str, Any]], item: str, category: str, merchant: str, amount: float,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    # Trusted merchants = those this user has had a HUMAN-APPROVED action with.
    # OK/MONITOR purchases alone do NOT grant trust (otherwise one cheap buy
    # launders a merchant).
    approved_merchants = {
        h["merchant"] for h in history
        if h.get("merchant") and h.get("resolution") == "APPROVED"
    }
    # Still useful for amount-baseline stats (real money movement), but not for trust.
    real = [h for h in history if _real(h)]
    flags: List[str] = []
    details: Dict[str, Any] = {}

    if amount is not None and amount > ABSOLUTE_AMOUNT_LIMIT:
        flags.append(AMOUNT_OVER_LIMIT_FLAG)
        details[AMOUNT_OVER_LIMIT_FLAG] = {"amount": amount, "limit": ABSOLUTE_AMOUNT_LIMIT}

    # Only HUMAN-APPROVED merchants count as "known" for trust purposes.
    known = approved_merchants
    burned = {h["merchant"] for h in history
              if h.get("status") == "HALTED" and h.get("resolution") != "APPROVED"} - approved_merchants

    if merchant in burned:
        flags.append(RETRY_AFTER_HALT_FLAG)
        details[RETRY_AFTER_HALT_FLAG] = {"merchant": merchant}

    if merchant not in known:
        # Cold start: even with empty history, an unknown merchant is flagged.
        flags.append(NEW_MERCHANT_FLAG)
        details[NEW_MERCHANT_FLAG] = {"merchant": merchant, "known_merchants_count": len(known)}

    # Hard rule: unknown merchant over cap
    if merchant not in known and amount is not None and amount > UNKNOWN_MERCHANT_CAP:
        flags.append(UNKNOWN_MERCHANT_OVER_CAP_FLAG)
        details[UNKNOWN_MERCHANT_OVER_CAP_FLAG] = {"merchant": merchant, "cap": UNKNOWN_MERCHANT_CAP}

    amounts = [h["amount"] for h in real if h.get("category") == category and h.get("amount") is not None]
    if len(amounts) >= MIN_HISTORY_FOR_AMOUNT_CHECK:
        m = mean(amounts)
        sd = max(pstdev(amounts), 0.25 * m, 1.0)
        z = (amount - m) / sd
        if z >= AMOUNT_ZSCORE_THRESHOLD:
            flags.append(AMOUNT_SPIKE_FLAG)
            details[AMOUNT_SPIKE_FLAG] = {"amount": amount, "historical_mean": round(m, 2), "z_score": round(z, 2)}

    cutoff = now - timedelta(minutes=FREQUENCY_WINDOW_MINUTES)
    recent = [h for h in history if (t := _ts(h)) is not None and t >= cutoff]
    if len(recent) >= FREQUENCY_MAX_ATTEMPTS:
        flags.append(FREQUENCY_SPIKE_FLAG)
        details[FREQUENCY_SPIKE_FLAG] = {"attempts_in_window": len(recent), "window_minutes": FREQUENCY_WINDOW_MINUTES}

    return {
        "prior_actions_count": len(history),
        "flags": flags,
        "details": details,
        "merchant_trusted": merchant in known,
    }
