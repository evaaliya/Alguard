"""
Two audiences, two views:
  - customer_message(): what the AGENT receives and may read aloud. Deliberately generic --
    no thresholds, no flag names, no severity, no hard-rule list. Otherwise the response
    is an oracle a hostile agent can bisect until a purchase passes.
  - internal_explanation(): full reason; shown ONLY on the authenticated Tier 2 screen.
Deterministic fallback; Bedrock path lives in bedrock_explain.py.
"""
from typing import Any, Dict


# explain.py
def customer_message(decision: Dict[str, Any], duplicate: bool = False) -> str:
    what = f"'{decision.get('item')}' (${decision.get('amount'):,.2f})"
    if duplicate:
        return (f"This exact request for {what} was already processed a moment ago, "
                f"so it was not repeated.")
    if decision["status"] == "HALTED":
        # Try Bedrock first (AWS Builder mini-challenge). Fall back to the
        # deterministic template -- Bedrock is never on the critical path.
        try:
            from mcp_server.utils.bedrock_explain import generate_customer_explanation
            llm_msg = generate_customer_explanation(decision)
        except Exception:
            llm_msg = None
        if llm_msg:
            return f"{llm_msg} (reference {decision['action_id']})"
        return (f"{what} has NOT been made. It needs the customer's own approval "
                f"in the Alexa app (reference {decision['action_id']}). "
                f"It cannot be approved through the assistant.")
    # OK and MONITOR land here:
    return f"{what} has been recorded."


def internal_explanation(decision: Dict[str, Any]) -> str:
    return (f"{decision['status']}: {decision.get('reason')} | risk_score={decision.get('risk_score')} "
            f"| severity={decision.get('category_severity')} | flags={decision.get('anomaly_flags')}")
