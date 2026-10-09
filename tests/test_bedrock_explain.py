"""Bedrock path always returns customer-ready HALT copy."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.utils.bedrock_explain import generate_customer_explanation
from mcp_server.utils.explain import customer_message


def test_bedrock_halt_message_is_never_empty(monkeypatch):
    monkeypatch.setenv("ALGUARD_BEDROCK_FORCE_SIM", "1")
    decision = {
        "action_id": "act_test",
        "item": "Gift card bundle",
        "amount": 500.0,
        "merchant": "random-giftcard-site",
        "status": "HALTED",
        "reason": "hard rule(s) triggered: high_risk_category",
        "category_severity": "High",
        "anomaly_flags": [],
        "risk_score": 0.65,
    }
    text, source = generate_customer_explanation(decision)
    assert text and "Alexa app" in text
    assert source == "bedrock-converse"
    msg = customer_message(decision)
    assert "act_test" in msg
    assert "Gift card" in msg or "500" in msg
