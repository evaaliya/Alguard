"""
Mirrors the one hard rule from Albugent's agent.py's generate_executive_summary:
an LLM (if used at all) only turns already-computed facts into a sentence. It never
sees raw tool access, never re-derives risk_score/status, and is explicitly told not
to invent numbers or facts beyond what it's given. The deterministic template below
is the fallback -- it's what runs if no LLM is configured, and what runs if the LLM
call fails for any reason. The decision (status/risk_score/reason) is ALWAYS computed
by risk_evaluator.py + circuit_breaker.py before this module ever runs; this module
only ever phrases it.

AWS Builder Mini Challenge: EXPLAIN_USE_BEDROCK=1 turns on a real Amazon Bedrock
Runtime call (Converse API, Amazon Nova by default) to generate the phrasing. This
is the same principle as Albugent's agent.py: the model gets a prompt built entirely
from precomputed facts and is told, in the prompt itself, not to add anything beyond
them. If Bedrock is unavailable, misconfigured, or the call fails for any reason,
this falls straight back to the deterministic template -- the customer never sees a
raw error instead of an explanation.
"""
import logging
import os
from typing import Any, Dict

logger = logging.getLogger("Alguard.explain")


def _deterministic_explanation(decision: Dict[str, Any]) -> str:
    status = decision["status"]
    item = decision.get("item", "an item")
    amount = decision.get("amount")
    merchant = decision.get("merchant")
    category = decision.get("category")

    base = f"Alexa+ attempted to purchase '{item}' (${amount}, category: {category}, merchant: {merchant})."

    if status == "OK":
        return base + " No risk signals were found; the purchase was completed automatically."

    if status == "MONITOR":
        flags = ", ".join(decision.get("anomaly_flags", [])) or "elevated category risk"
        return (
            base + f" It was completed, but flagged for review because: {flags}. "
            f"reason: {decision.get('reason')}"
        )

    # HALTED
    return (
        base + " It was NOT completed and requires your explicit approval because: "
        f"{decision.get('reason')}. Approve or deny this from your Alexa app, not through the assistant."
    )


def _bedrock_explanation(decision: Dict[str, Any]) -> str:
    """
    Raises on any failure (missing boto3, no AWS creds, model access denied, network
    error, empty response) -- the caller (explain_decision) catches broadly and falls
    back to the deterministic template. Never allowed to invent a status or a number
    the risk engine didn't already compute.
    """
    import boto3  # imported lazily so boto3 stays optional unless this path is used

    region = os.environ.get("AWS_REGION", "us-east-1")
    model_id = os.environ.get("AWS_BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")

    facts = (
        f"status: {decision['status']}\n"
        f"item: {decision.get('item')}\n"
        f"amount: {decision.get('amount')}\n"
        f"category: {decision.get('category')}\n"
        f"merchant: {decision.get('merchant')}\n"
        f"risk_score: {decision.get('risk_score')}\n"
        f"reason: {decision.get('reason')}\n"
        f"anomaly_flags: {decision.get('anomaly_flags')}\n"
    )
    prompt = (
        "Write ONE short, plain-language sentence (or two at most) explaining this purchase "
        "decision to the customer, based ONLY on the facts below. Do not invent, estimate, or "
        "add any number, status, or reason not already given here. If status is HALTED, tell "
        "the customer it needs their approval in the Alexa app, not through the assistant. "
        "Output ONLY the explanation text -- no headers, no bullet points, no preamble.\n\n"
        f"{facts}"
    )

    client = boto3.client("bedrock-runtime", region_name=region)
    response = client.converse(
        modelId=model_id,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 150, "temperature": 0.3},
    )
    text = response["output"]["message"]["content"][0]["text"].strip()
    if not text:
        raise ValueError("Bedrock returned an empty explanation")
    return text


def explain_decision(decision: Dict[str, Any]) -> str:
    if os.environ.get("EXPLAIN_USE_BEDROCK") == "1":
        try:
            return _bedrock_explanation(decision)
        except Exception as e:
            logger.warning(
                "Bedrock explanation failed for action=%s, falling back to template: %s",
                decision.get("action_id"), e,
            )
    return _deterministic_explanation(decision)