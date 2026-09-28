"""
Structural port of Albugent's risk_evaluator.py.
There: risk_score = pii(0.4) + freshness(<=0.4) + centrality(*0.2), thresholded at 0.65.
Here:  risk_score = category_severity(0.45) + anomaly_signals(<=0.4) + (1-trust)*0.2,
       same 0.65 "high risk" threshold, same alias-key discipline in the return dict
       so downstream code (context_builder, mcp_server) can read consistent field names.

On top of the weighted score there is ONE hard rule that the score can never talk its
way around: any single purchase above ABSOLUTE_AMOUNT_LIMIT is always halted, whatever
the category, whatever the merchant history, whatever the agent's trust score. The
weighted score is deliberately soft (trust and familiarity lower it); this rule is
deliberately not.
"""
from typing import Any, Dict, List

from mcp_server.utils.category_risk import detect_risky_category, classify_category_severity

SEVERITY_WEIGHT = {"High": 0.45, "Medium": 0.25, "Low": 0.1}
ANOMALY_WEIGHT = 0.15  # per independent flag, capped at 0.4 total below
ANOMALY_CAP = 0.4
TRUST_WEIGHT = 0.2
HIGH_RISK_THRESHOLD = 0.65
ABSOLUTE_AMOUNT_LIMIT = 1000.0  # USD; strictly above this always requires human approval


def evaluate_purchase_risk(
    category: str = "",
    amount: float = 0.0,
    anomaly_flags: List[str] = None,
    trust_score: float = 0.5,
    **kwargs,
) -> Dict[str, Any]:
    anomaly_flags = anomaly_flags or []

    matched_keywords = detect_risky_category(category)
    severity = classify_category_severity(category)
    category_score = SEVERITY_WEIGHT.get(severity, 0.1)

    anomaly_score = min(len(anomaly_flags) * ANOMALY_WEIGHT, ANOMALY_CAP)
    trust_score = max(0.0, min(1.0, trust_score))
    trust_penalty = (1.0 - trust_score) * TRUST_WEIGHT

    total_risk = round(min(category_score + anomaly_score + trust_penalty, 1.0), 2)

    # Hard rule: the score is floored at the halt threshold, so "HALTED <=> risk_score
    # >= threshold" stays true everywhere downstream.
    over_limit = amount is not None and amount > ABSOLUTE_AMOUNT_LIMIT
    if over_limit:
        total_risk = max(total_risk, HIGH_RISK_THRESHOLD)

    return {
        "risk_score": total_risk,
        "category": category,
        "category_severity": severity,
        "matched_risk_keywords": matched_keywords,
        "anomaly_flags": anomaly_flags,     # alias kept consistent with session_profiler's key
        "trust_score": trust_score,
        "over_absolute_limit": over_limit,
        "absolute_amount_limit": ABSOLUTE_AMOUNT_LIMIT,
        "amount": amount,
        "is_high_risk": total_risk >= HIGH_RISK_THRESHOLD,
    }