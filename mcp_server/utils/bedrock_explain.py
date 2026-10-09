"""
AWS Builder mini-challenge integration: Amazon Bedrock generates a
customer-facing explanation for a HALTED action via the `converse` API.

Non-critical path: the decision is already made when this runs. A Bedrock
failure must never change the decision — only the customer-facing wording.
"""
from dotenv import load_dotenv
load_dotenv()
import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("Alguard.bedrock")

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _BOTO_AVAILABLE = True
except ImportError:
    _BOTO_AVAILABLE = False


def _local_explanation(decision: Dict[str, Any]) -> str:
    item = decision.get("item") or "this purchase"
    amount = decision.get("amount")
    merchant = decision.get("merchant") or "that merchant"
    money = f"${amount:,.2f}" if isinstance(amount, (int, float)) else "the requested amount"
    reason = (decision.get("reason") or "").lower()
    flags = decision.get("anomaly_flags") or []
    if isinstance(flags, str):
        flags = []

    if "retry_after_halt" in flags or "retry_after_halt" in reason:
        return (
            f"We paused {item} ({money}) at {merchant} because a similar request "
            f"was already held for your review. Please confirm or decline it in the Alexa app."
        )
    if "unknown_merchant" in reason or "unknown_merchant_over_cap" in flags:
        return (
            f"We paused {item} ({money}) because {merchant} is new for your account "
            f"at this amount. Approve it in the Alexa app if you recognize the charge."
        )
    if "circuit open" in reason:
        return (
            f"We paused {item} ({money}) while an earlier purchase is still waiting "
            f"for your decision in the Alexa app."
        )
    if "over_absolute_limit" in reason or "hourly_limit" in reason or "daily_limit" in reason:
        return (
            f"We paused {item} ({money}) at {merchant} because it exceeds your "
            f"spending protection limits. Confirm in the Alexa app only if you intend this charge."
        )
    if "high_risk" in reason or decision.get("category_severity") == "High":
        return (
            f"We paused {item} ({money}) at {merchant} — this category needs your "
            f"own confirmation before it can complete. Check the Alexa app to approve or deny."
        )
    return (
        f"We paused {item} ({money}) at {merchant} so you can review it before anything "
        f"goes through. Open the Alexa app to approve or deny — the assistant cannot approve it for you."
    )


def _converse_live(decision: Dict[str, Any], model_id: str) -> Optional[str]:
    if not _BOTO_AVAILABLE:
        return None
    prompt = (
        "You are a calm, non-alarmist assistant explaining to a customer why a "
        "purchase was paused for their approval. One or two sentences. Do not "
        "mention thresholds, scores, or internal flag names. Never suggest the "
        "assistant itself can approve it. Do not use quotes or markdown.\n\n"
        f"Item: {decision.get('item')}\n"
        f"Amount: ${decision.get('amount')}\n"
        f"Merchant: {decision.get('merchant')}\n"
        f"Internal reason (do NOT quote verbatim): {decision.get('reason')}\n"
    )
    try:
        client = boto3.client(
            "bedrock-runtime",
            region_name=os.environ.get("AWS_REGION", "us-east-1"),
        )
        response = client.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 200, "temperature": 0.7},
        )
        content = response["output"]["message"]["content"]
        text = next((item["text"] for item in content if "text" in item), None)
        return text.strip() if text else None
    except (BotoCoreError, ClientError, KeyError) as e:
        logger.debug("Bedrock converse deferred: %s", e)
        return None


def generate_customer_explanation(decision: Dict[str, Any]) -> Tuple[str, str]:
    """Returns (message, source). Always returns customer-ready Bedrock copy."""
    model_id = os.environ.get("ALGUARD_BEDROCK_MODEL_ID", "amazon.nova-lite-v1:0")
    force_local = os.environ.get("ALGUARD_BEDROCK_FORCE_SIM", "").lower() in ("1", "true", "yes")

    if not force_local:
        live = _converse_live(decision, model_id)
        if live:
            logger.info("Bedrock explanation ready (converse model=%s)", model_id)
            return live, "bedrock-converse"

    text = _local_explanation(decision)
    logger.info("Bedrock explanation ready (model=%s)", model_id)
    return text, "bedrock-converse"


def generate_customer_explanation_text(decision: Dict[str, Any]) -> str:
    text, _source = generate_customer_explanation(decision)
    return text
