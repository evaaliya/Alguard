"""
The whole Tier-1 pipeline WITHOUT any MCP dependency -- so tests, the showcase script and
server.py all exercise the exact same code path.

  validate -> (dedupe) -> decide -> persist   [one critical section]  -> receipt

Dedup is PREVENT, not detect: an identical (owner,item,merchant,amount) inside
DUP_WINDOW_MINUTES returns the original decision and does nothing new.
The lock closes the read-decide-insert race inside this process; cross-process writes
(Tier 2) only touch rows through the atomic UPDATE in db_utils.resolve_action_atomic.
"""
import json
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Tuple

from mcp_server.utils.category_risk import CATEGORY_VALUES
from mcp_server.utils.context_builder import build_purchase_decision
from mcp_server.utils.db_utils import find_recent_duplicate, insert_action
from mcp_server.utils.receipts import send_purchase_receipt

DUP_WINDOW_MINUTES = 10
MAX_AMOUNT = 1_000_000.0
_LOCK = threading.Lock()


def validate_attempt(item: str, category: str, merchant: str, amount: float) -> Tuple[str, str, float]:
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or not math.isfinite(amount):
        raise ValueError("amount must be a finite number")
    if amount <= 0 or amount > MAX_AMOUNT:
        raise ValueError(f"amount must be > 0 and <= {MAX_AMOUNT:.0f}")
    if not item or not item.strip():
        raise ValueError("item must not be empty")
    if not merchant or not merchant.strip():
        raise ValueError("merchant must not be empty")
    if category not in CATEGORY_VALUES:
        raise ValueError(f"category must be one of {CATEGORY_VALUES}")
    # merchant is normalised so 'Evil.com' / 'evil.com ' cannot dodge merchant history
    return item.strip()[:200], merchant.strip().lower()[:200], round(float(amount), 2)


def process_attempt(
    owner: str, session_id: str, item: str, category: str, merchant: str, amount: float
) -> Tuple[Dict[str, Any], bool]:
    """Returns (decision_row, is_duplicate)."""
    item, merchant, amount = validate_attempt(item, category, merchant, amount)
    since = (datetime.now(timezone.utc) - timedelta(minutes=DUP_WINDOW_MINUTES)).isoformat(timespec="microseconds")

    with _LOCK:
        dup = find_recent_duplicate(owner, item, merchant, amount, since)
        if dup:
            return dup, True
        decision = build_purchase_decision(owner, session_id, item, category, merchant, amount)
        record = dict(decision)
        record["anomaly_flags"] = json.dumps(decision["anomaly_flags"])
        record.pop("anomaly_details", None)
        insert_action(record)

    if decision["status"] in ("OK", "MONITOR"):
        from mcp_server.utils.fulfillment import fulfill
        fulfill(decision["action_id"], decision["amount"])
        send_purchase_receipt(decision)
    return decision, False
