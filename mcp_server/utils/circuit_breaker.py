"""
A circuit breaker that can actually open.

Old behaviour: a prior HALT in the *session* raised later steps to MONITOR, and MONITOR
executes -- so it only changed a label, and a new session_id reset it.

Now the circuit is per OWNER (verified user), not per session_id the agent chooses:
while this user has an OPEN halt -- an unresolved HALTED, or one the human DENIED, within
CIRCUIT_WINDOW_HOURS -- any further attempt of Medium/High effective severity is HALTED,
and Low-severity ones are at least MONITOR. A human APPROVAL closes the circuit.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

STATUS_RANK = {"OK": 0, "MONITOR": 1, "HALTED": 2}
CIRCUIT_WINDOW_HOURS = 24


def _own_status(r: Dict[str, Any]) -> Tuple[str, str]:
    hard = r.get("hard_halt_reasons") or []
    if hard:
        msg = f"hard rule(s) triggered: {', '.join(hard)}"
        if "over_absolute_limit" in hard:
            msg += f" (amount ${r.get('amount'):,.2f} > ${r.get('absolute_amount_limit'):,.0f} limit)"
        return "HALTED", msg
    if r.get("is_high_risk"):
        return "HALTED", (f"risk_score {r['risk_score']} >= threshold "
                          f"(severity={r.get('category_severity')}, flags={r.get('anomaly_flags')})")
    if r.get("anomaly_flags"):
        return "MONITOR", f"anomaly_flags present: {r.get('anomaly_flags')}"
    return "OK", "no risk signals"


def _open_halt(
    recent_owner_actions: List[Dict[str, Any]],
    now: datetime,
) -> Optional[Dict[str, Any]]:
    """
    A halt keeps the circuit open only while it is genuinely blocking something:

      - resolution is None  AND expires_at > now   (still approvable — pending)
      - resolution == DENIED                       (human said no — hold the window)

    An unresolved halt whose expires_at has passed no longer holds the circuit:
    the customer can no longer approve it, so it must not lock them out.
    The halt row stays in history (used by retry_after_halt / audit).
    """
    for a in recent_owner_actions:
        if a.get("status") != "HALTED":
            continue
        res = a.get("resolution")
        if res == "DENIED":
            return a
        if res is None:
            exp = a.get("expires_at")
            if not exp:
                # No TTL recorded → treat as pending (defensive default).
                return a
            try:
                exp_dt = datetime.fromisoformat(exp)
            except (TypeError, ValueError):
                return a
            if exp_dt > now:
                return a
    return None


def compute_action_status(
    risk_result: Dict[str, Any],
    recent_owner_actions: List[Dict[str, Any]],
    now: Optional[datetime] = None,
) -> Tuple[str, str]:
    """
    `recent_owner_actions`: this owner's actions within CIRCUIT_WINDOW_HOURS.
    `now`: passed in for testability; defaults to current UTC.
    """
    now = now or datetime.now(timezone.utc)
    own_status, own_reason = _own_status(risk_result)
    open_halt = _open_halt(recent_owner_actions, now)
    if open_halt is None or own_status == "HALTED":
        return own_status, own_reason

    state = "pending" if open_halt.get("resolution") is None else "denied"
    if risk_result.get("category_severity") != "Low":
        return "HALTED", (f"circuit open: earlier action {open_halt['action_id']} is {state}; "
                          f"non-Low-severity purchases are held until the customer resolves it")
    if STATUS_RANK[own_status] >= STATUS_RANK["MONITOR"]:
        return own_status, own_reason
    return "MONITOR", f"circuit open: earlier action {open_halt['action_id']} is {state}"
