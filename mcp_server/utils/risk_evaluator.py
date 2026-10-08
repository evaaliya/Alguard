"""
risk_score = severity(0.45/0.25/0.10) + anomalies(<=0.4) + (1-trust)*0.2, threshold 0.65.

The weighted score is deliberately SOFT. On top of it sit HARD rules that no amount of
trust, history or clever scoring can talk around (each forces HALTED):
  - single purchase above ABSOLUTE_AMOUNT_LIMIT
  - rolling 1h / 24h cumulative spend above HOURLY_LIMIT / DAILY_LIMIT
  - effective severity High (gift_card, wire_transfer, gambling, crypto, loan) -- this is
    exactly what addon.json promises the customer
  - retry_after_halt: same merchant was already halted and never approved by the human
Severity is computed by the SERVER (declared category + text inference + unknown-merchant
floor), not taken from the agent's word.
"""
from typing import Any, Dict, List

from mcp_server.utils.category_risk import (
    classify_category_severity, detect_risky_category, infer_category_from_text,
    max_severity, SEVERITY_ORDER,
)

SEVERITY_WEIGHT = {"High": 0.45, "Medium": 0.25, "Low": 0.1}
ANOMALY_WEIGHT = 0.15
ANOMALY_CAP = 0.4
TRUST_WEIGHT = 0.2
HIGH_RISK_THRESHOLD = 0.65
ABSOLUTE_AMOUNT_LIMIT = 1000.0
HOURLY_LIMIT = 1500.0
DAILY_LIMIT = 3000.0


def evaluate_purchase_risk(
    category: str = "",
    amount: float = 0.0,
    anomaly_flags: List[str] = None,
    trust_score: float = 0.5,
    item: str = "",
    merchant: str = "",
    merchant_trusted: bool = False,
    spend_1h: float = 0.0,
    spend_24h: float = 0.0,
    **kwargs,
) -> Dict[str, Any]:
    flags = list(anomaly_flags or [])

    declared = classify_category_severity(category)
    inferred_cat = infer_category_from_text(item, merchant)
    inferred = classify_category_severity(inferred_cat) if inferred_cat else "Low"
    if inferred_cat and SEVERITY_ORDER[inferred] > SEVERITY_ORDER[declared] and "category_mismatch" not in flags:
        flags.append("category_mismatch")  # the agent under-declared
    severity = max_severity(declared, inferred, "Low" if merchant_trusted else "Medium")

    anomaly_score = min(len(flags) * ANOMALY_WEIGHT, ANOMALY_CAP)
    trust_score = max(0.0, min(1.0, trust_score))
    total_risk = round(min(SEVERITY_WEIGHT[severity] + anomaly_score + (1.0 - trust_score) * TRUST_WEIGHT, 1.0), 2)

    over_limit = amount is not None and amount > ABSOLUTE_AMOUNT_LIMIT
    hard: List[str] = []
    if over_limit:
        hard.append("over_absolute_limit")
    if spend_1h + amount > HOURLY_LIMIT:
        hard.append("hourly_limit")
    if spend_24h + amount > DAILY_LIMIT:
        hard.append("daily_limit")
    if severity == "High":
        hard.append("high_risk_category")
    if "retry_after_halt" in flags:
        hard.append("retry_after_halt")
    if hard:
        total_risk = max(total_risk, HIGH_RISK_THRESHOLD)  # keeps HALTED <=> score >= threshold

    return {
        "risk_score": total_risk,
        "category": category,
        "category_severity": severity,
        "inferred_category": inferred_cat,
        "matched_risk_keywords": detect_risky_category(category),
        "anomaly_flags": flags,
        "trust_score": trust_score,
        "hard_halt_reasons": hard,
        "over_absolute_limit": over_limit,
        "absolute_amount_limit": ABSOLUTE_AMOUNT_LIMIT,
        "amount": amount,
        "is_high_risk": total_risk >= HIGH_RISK_THRESHOLD,
    }
