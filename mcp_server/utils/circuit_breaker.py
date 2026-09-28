"""
Structural port of Albugent's circuit_breaker.py.
There: HALT propagates forward across a lineage graph (raw -> staging -> mart) whenever
an anomalous column overlaps with a downstream table's columns.
Here: HALT propagates forward across time within one Alexa+ session — once a step in a
session is HALTED, every later step in that same session inherits at least MONITOR,
even if that later step's own risk score looks fine in isolation. Session steps are a
simple chain (chronological order), not a general graph, so propagation is a linear scan
instead of a DFS.
"""
from typing import Any, Dict, List, Tuple

STATUS_RANK = {"OK": 0, "MONITOR": 1, "HALTED": 2}


def _own_status(risk_result: Dict[str, Any]) -> Tuple[str, str]:
    if risk_result.get("over_absolute_limit"):
        return "HALTED", (
            f"amount ${risk_result.get('amount'):,.2f} is above the "
            f"${risk_result.get('absolute_amount_limit'):,.0f} absolute limit -- "
            f"purchases above it always need your approval, whatever the category"
        )
    if risk_result.get("is_high_risk"):
        return "HALTED", (
            f"risk_score {risk_result['risk_score']} >= threshold "
            f"(category_severity={risk_result.get('category_severity')}, "
            f"anomaly_flags={risk_result.get('anomaly_flags')})"
        )
    if risk_result.get("anomaly_flags"):
        return "MONITOR", f"anomaly_flags present: {risk_result.get('anomaly_flags')}"
    return "OK", "no risk signals"


def compute_action_status(
    risk_result: Dict[str, Any], prior_session_actions: List[Dict[str, Any]]
) -> Tuple[str, str]:
    """
    Returns (status, reason) for the CURRENT action, given its own risk evaluation and
    the already-logged actions earlier in the same session (chronological order).
    """
    own_status, own_reason = _own_status(risk_result)

    halted_predecessor = next(
        (a for a in prior_session_actions if a.get("status") == "HALTED"), None
    )
    if halted_predecessor is None:
        return own_status, own_reason

    inherited_status = "MONITOR"  # a HALT never silently escalates a later step to HALTED
    if STATUS_RANK[own_status] >= STATUS_RANK[inherited_status]:
        return own_status, own_reason

    return inherited_status, (
        f"inherited MONITOR: prior action {halted_predecessor['action_id']} "
        f"in this session was HALTED ({halted_predecessor.get('reason')})"
    )