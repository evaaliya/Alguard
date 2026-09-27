"""
Alexa+ Functional Requirements, section 8 (Transaction Flow), Payment & Commitment:

    "Ensure your add-on sends a separate purchase confirmation to the customer via
    email or other written channel"
    "Deliver a receipt via at least one channel (notification, email, or partner
    account) with correct transaction details after payment."

This now actually delivers on at least one channel, for real, with no external
account needed:

  1. A local receipts ledger (data/receipts_ledger.jsonl) -- this IS the "partner
     account" channel: a durable, queryable record of every completed purchase's
     receipt, the same shape a real order-history page would read from. Always
     works, no configuration required. web_api/resolve_action.py exposes it at
     GET /receipts/{action_id} so it's actually viewable, not just written to disk.
  2. Optional real email via SMTP (stdlib smtplib, no new dependency) if SMTP_HOST
     is configured -- a genuine second channel, not a stub, once you point it at a
     real mail server.

STATUS / honest limitation: we don't have a real per-customer email address to send
to -- that requires Account Linking (see auth_context.py), which isn't wired up yet.
RECEIPT_TO_EMAIL is a single stand-in recipient for testing the email path, not a
per-customer address. Replace it with the linked customer's address once Account
Linking exists.
"""
import json
import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Dict, Optional

logging.getLogger("Alguard.receipts")

RECEIPTS_LEDGER_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "receipts_ledger.jsonl"


def _append_to_ledger(decision: Dict[str, Any]) -> None:
    """The always-on channel -- a durable receipt record, no config needed."""
    RECEIPTS_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "action_id": decision.get("action_id"),
        "item": decision.get("item"),
        "amount": decision.get("amount"),
        "merchant": decision.get("merchant"),
        "status": decision.get("status"),
        "created_at": decision.get("created_at"),
    }
    with open(RECEIPTS_LEDGER_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def get_receipt(action_id: str) -> Optional[Dict[str, Any]]:
    """Read one receipt back out of the ledger, for the GET /receipts/{id} endpoint."""
    if not RECEIPTS_LEDGER_PATH.exists():
        return None
    with open(RECEIPTS_LEDGER_PATH, "r", encoding="utf-8") as f:
        for line in f:
            entry = json.loads(line)
            if entry.get("action_id") == action_id:
                return entry
    return None


def _send_email_receipt(decision: Dict[str, Any]) -> bool:
    """The optional real channel. Returns True only if actually sent."""
    host = os.environ.get("SMTP_HOST")
    if not host:
        return False

    to_addr = os.environ.get("RECEIPT_TO_EMAIL")
    if not to_addr:
        logger.warning("SMTP_HOST is set but RECEIPT_TO_EMAIL is not -- skipping email receipt")
        return False

    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("RECEIPT_FROM_EMAIL", user or "no-reply@example.com")

    msg = EmailMessage()
    msg["Subject"] = f"Receipt for {decision.get('item')} - {decision.get('action_id')}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(
        f"Your purchase was {decision.get('status')}.\n\n"
        f"Item: {decision.get('item')}\n"
        f"Amount: ${decision.get('amount')}\n"
        f"Merchant: {decision.get('merchant')}\n"
        f"Action ID: {decision.get('action_id')}\n"
    )

    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        return True
    except Exception as e:
        logger.error("Email receipt failed for action=%s: %s", decision.get("action_id"), e)
        return False


def send_purchase_receipt(decision: Dict[str, Any]) -> None:
    """
    Call this for every action that actually completes (status OK or MONITOR -- never
    for HALTED, since nothing was purchased yet) and again once a Tier 2 approval
    turns a HALTED action into a completed one.
    """
    _append_to_ledger(decision)
    email_sent = _send_email_receipt(decision)
    logger.info(
        "Receipt delivered for action=%s (ledger=yes, email=%s)",
        decision.get("action_id"), email_sent,
    )