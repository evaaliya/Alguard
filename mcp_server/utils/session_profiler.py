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
  - amount_spike uses a stdev floor (25% of the mean) so 10,10,10 -> 12 is not a spike;
  - merchant trust comes only from a human APPROVED resolution OR the user's own
    allowlist (`user_trusted_merchants`), never from a static allowlist.
"""
from datetime import datetime, timedelta, timezone
from statistics import mean, pstdev
from typing import Any, Dict, List, Optional, Set

from mcp_server.utils.category_risk import TRUSTED_MERCHANTS, UNKNOWN_MERCHANT_CAP
from mcp_server.utils.risk_evaluator import ABSOLUTE_AMOUNT_LIMIT
from mcp_server.utils.circuit_breaker import CIRCUIT_WINDOW_HOURS

NEW_MERCHANT_FLAG = "new_merchant"
AMOUNT_SPIKE_FLAG = "amount_spike"
FREQUENCY_SPIKE_FLAG = "frequency_spike"
AMOUNT_OVER_LIMIT_FLAG = "amount_over_limit"
RETRY_AFTER_HALT_FLAG = "retry_after_halt"
UNKNOWN_MERCHANT_OVER_CAP_FLAG = "unknown_merchant_over_cap"
BANNED_MERCHANT_FLAG = "banned_merchant"

MIN_HISTORY_FOR_AMOUNT_CHECK = 3
AMOUNT_ZSCORE_THRESHOLD = 2.0
FREQUENCY_WINDOW_MINUTES = 10
FREQUENCY_MAX_ATTEMPTS = 5


def _real(h: Dict[str, Any]) -> bool:
    """Did this action actually happen, or was it approved by a human?"""
    return h.get("status") in ("OK", "MONITOR") or h.get("resolution") == "APPROVED"


def _ts(h: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(h["created_at"])
    except Exception:
        return None


def profile_purchase_attempt(
    history: List[Dict[str, Any]],
    item: str,
    category: str,
    merchant: str,
    amount: float,
    now: Optional[datetime] = None,
    user_trusted_merchants: Optional[Set[str]] = None,
    user_banned_merchants: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    flags: List[str] = []
    details: Dict[str, Any] = {}

    if amount is not None and amount > ABSOLUTE_AMOUNT_LIMIT:
        flags.append(AMOUNT_OVER_LIMIT_FLAG)
        details[AMOUNT_OVER_LIMIT_FLAG] = {"amount": amount, "limit": ABSOLUTE_AMOUNT_LIMIT}

    if merchant in (user_banned_merchants or set()):
        flags.append(BANNED_MERCHANT_FLAG)
        details[BANNED_MERCHANT_FLAG] = {"merchant": merchant}

    # ---- merchant trust ---------------------------------------------------------------
    # "known" = human APPROVED this merchant before, OR the user explicitly allowlisted it.
    approved_merchants = {
        h["merchant"] for h in history
        if h.get("merchant") and h.get("resolution") == "APPROVED"
    }
    known = approved_merchants | (user_trusted_merchants or set())

    # burned = halted & never approved nor allowlisted, within the circuit window, and NOT circuit-caused
    cutoff_burn = now - timedelta(hours=CIRCUIT_WINDOW_HOURS)

    def _is_recent(h: Dict[str, Any]) -> bool:
        try:
            return datetime.fromisoformat(h["created_at"]) >= cutoff_burn
        except Exception:
            return False

    burned = {
        h["merchant"] for h in history
        if h.get("merchant")
        and h.get("status") == "HALTED"
        and h.get("resolution") != "APPROVED"
        and h.get("halt_cause") != "circuit"
        and _is_recent(h)
    } - approved_merchants - (user_trusted_merchants or set())

    if merchant in burned:
        flags.append(RETRY_AFTER_HALT_FLAG)
        details[RETRY_AFTER_HALT_FLAG] = {"merchant": merchant}
    elif merchant not in known:
        # Cold start: even with empty history, an unknown merchant is flagged.
        flags.append(NEW_MERCHANT_FLAG)
        details[NEW_MERCHANT_FLAG] = {"merchant": merchant, "known_merchants_count": len(known)}
        # New hard rule: unknown merchant + amount over cap -> halt.
        if amount is not None and amount > UNKNOWN_MERCHANT_CAP:
            flags.append(UNKNOWN_MERCHANT_OVER_CAP_FLAG)
            details[UNKNOWN_MERCHANT_OVER_CAP_FLAG] = {"merchant": merchant, "cap": UNKNOWN_MERCHANT_CAP}

    # ---- amount spike ------------------------------------------------------------------
    amounts = [
        h["amount"] for h in history
        if h.get("category") == category and h.get("amount") is not None and _real(h)
    ]
    if len(amounts) >= MIN_HISTORY_FOR_AMOUNT_CHECK:
        m = mean(amounts)
        sd = max(pstdev(amounts), 0.25 * m, 1.0)
        z = (amount - m) / sd
        if z >= AMOUNT_ZSCORE_THRESHOLD:
            flags.append(AMOUNT_SPIKE_FLAG)
            details[AMOUNT_SPIKE_FLAG] = {
                "amount": amount, "historical_mean": round(m, 2), "z_score": round(z, 2)
            }

    # ---- frequency ----------------------------------------------------------------------
    cutoff = now - timedelta(minutes=FREQUENCY_WINDOW_MINUTES)
    recent = [h for h in history if (t := _ts(h)) is not None and t >= cutoff]
    if len(recent) >= FREQUENCY_MAX_ATTEMPTS:
        flags.append(FREQUENCY_SPIKE_FLAG)
        details[FREQUENCY_SPIKE_FLAG] = {
            "attempts_in_window": len(recent), "window_minutes": FREQUENCY_WINDOW_MINUTES
        }

    return {
        "prior_actions_count": len(history),
        "flags": flags,
        "details": details,
        "merchant_trusted": merchant in known,
    }