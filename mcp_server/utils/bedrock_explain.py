"""
AWS Builder mini-challenge integration: Amazon Bedrock generates a
customer-facing explanation for a HALTED action. Optional -- falls back to
the deterministic template in explain.py if Bedrock is unreachable.

This is a non-critical path: the decision is already made by the time we
call Bedrock. A Bedrock failure must never change the decision.

Uses the Bedrock `converse` API (the 2026 replacement for the older
`invoke_model` + anthropic_version wrapper, which no longer accepts newer
Claude models such as Haiku 4.5).
"""
from dotenv import load_dotenv
load_dotenv()
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("Alguard.bedrock")

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    _BOTO_AVAILABLE = True
except ImportError:
    _BOTO_AVAILABLE = False


def generate_customer_explanation(decision: Dict[str, Any]) -> Optional[str]:
    """
    Returns a Bedrock-generated explanation, or None if Bedrock is
    unavailable (caller falls back to explain.customer_message()).
    """
    model_id = os.environ.get("ALGUARD_BEDROCK_MODEL_ID")
    if not _BOTO_AVAILABLE or not model_id:
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
        logger.warning("Bedrock explanation failed (non-fatal): %s", e)
        return None