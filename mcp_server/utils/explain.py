"""
Two audiences, two views:
  - customer_message(): what the AGENT receives and may read aloud. Deliberately generic --
    no thresholds, no flag names, no severity, no hard-rule list. Otherwise the response
    is an oracle a hostile agent can bisect until a purchase passes.
  - internal_explanation(): full reason; shown ONLY on the authenticated Tier 2 screen.

HALTED customer copy comes from Amazon Bedrock
(`bedrock_explain.generate_customer_explanation`). Bedrock is never on the
critical decision path — only the wording.
"""
from typing import Any, Dict


def customer_message(decision: Dict[str, Any], duplicate: bool = False) -> str:
    what = f"'{decision.get('item')}' (${decision.get('amount'):,.2f})"

    # A duplicate of a HALTED action must return the SAME approval-pending
    # message as the original: the customer must see "needs your approval",
    # not "already processed" (which reads as "everything is done").
    # Duplicate only changes the message for OK/MONITOR, where the outcome
    # really was already recorded.
    if duplicate and decision["status"] != "HALTED":
        return (f"This exact request for {what} was already processed a moment ago, "
                f"so it was not repeated.")

    if decision["status"] == "HALTED":
        from mcp_server.utils.bedrock_explain import generate_customer_explanation
        llm_msg, _source = generate_customer_explanation(decision)
        return f"{llm_msg} (reference {decision['action_id']})"

    # OK and MONITOR land here:
    return f"{what} has been recorded (simulated, no real charge)."


def internal_explanation(decision: Dict[str, Any]) -> str:
    return (f"{decision['status']}: {decision.get('reason')} | risk_score={decision.get('risk_score')} "
            f"| severity={decision.get('category_severity')} | flags={decision.get('anomaly_flags')}")
