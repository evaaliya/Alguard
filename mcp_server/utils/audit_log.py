"""Append-only audit log (JSONL, WORM-style).

Separate from the mutable `actions` state table so that rewriting history is not
possible through any code path: this file is opened only in append mode ("a"), and
no function here (or elsewhere) opens it for write/truncate/delete. The agent
channel has no MCP tool that touches it.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

AUDIT_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "audit_log.jsonl"


def append_audit_event(event: str, action_id: str, owner: str, detail: Dict[str, Any]) -> None:
    """Append one immutable audit line. Never raises for missing dirs (creates it)."""
    AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "event": event,
        "action_id": action_id,
        "owner": owner,
        **detail,
    }
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
