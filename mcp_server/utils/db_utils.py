"""
Thin sqlite helpers. Every read is scoped to an owner (verified end-user subject);
there is no function here that returns another user's rows. Resolution is a single
atomic UPDATE (no check-then-act race).
"""
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "actions_log.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "schema.sql"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")   # Tier 1 and Tier 2 are separate processes
    conn.execute("PRAGMA busy_timeout=10000;")
    return conn


def ensure_schema() -> None:
    conn = get_connection()
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    # fixed-width (microseconds always present) so ISO strings sort lexicographically
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _rows(sql: str, params: tuple) -> List[Dict[str, Any]]:
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


# ---- trust -------------------------------------------------------------------------

def get_agent_trust(agent_id: str) -> float:
    rows = _rows("SELECT trust_score FROM agent_trust WHERE agent_id = ?;", (agent_id,))
    return float(rows[0]["trust_score"]) if rows else 0.5


def bump_agent_trust(agent_id: str, approved: bool) -> None:
    """Tier 2 only. Denials cost more than approvals earn."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM agent_trust WHERE agent_id = ?;", (agent_id,)).fetchone()
        trust, ap, de = (0.5, 0, 0) if row is None else (row["trust_score"], row["approved_count"], row["denied_count"])
        if approved:
            ap += 1; trust = min(1.0, trust + 0.03)
        else:
            de += 1; trust = max(0.0, trust - 0.15)
        conn.execute(
            """INSERT INTO agent_trust (agent_id, trust_score, approved_count, denied_count, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(agent_id) DO UPDATE SET trust_score=excluded.trust_score,
                 approved_count=excluded.approved_count, denied_count=excluded.denied_count,
                 updated_at=excluded.updated_at;""",
            (agent_id, trust, ap, de, now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


# ---- owner-scoped reads --------------------------------------------------------------

def get_owner_actions(owner: str, limit: int = 500) -> List[Dict[str, Any]]:
    """Newest `limit` actions of this owner, returned oldest-first."""
    rows = _rows(
        "SELECT * FROM actions WHERE agent_id = ? ORDER BY created_at DESC LIMIT ?;", (owner, limit)
    )
    return list(reversed(rows))


def get_session_actions(session_id: str, owner: str) -> List[Dict[str, Any]]:
    return _rows(
        "SELECT * FROM actions WHERE session_id = ? AND agent_id = ? ORDER BY created_at ASC;",
        (session_id, owner),
    )


def get_action(action_id: str, owner: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """With `owner`, a foreign action is indistinguishable from a missing one."""
    if owner is None:
        rows = _rows("SELECT * FROM actions WHERE action_id = ?;", (action_id,))
    else:
        rows = _rows("SELECT * FROM actions WHERE action_id = ? AND agent_id = ?;", (action_id, owner))
    return rows[0] if rows else None


def spend_since(owner: str, since_iso: str) -> float:
    """Money that actually went (or was approved to go) out since `since_iso`."""
    rows = _rows(
        """SELECT COALESCE(SUM(amount), 0) AS s FROM actions
           WHERE agent_id = ? AND created_at >= ?
             AND (status IN ('OK','MONITOR') OR resolution = 'APPROVED');""",
        (owner, since_iso),
    )
    return float(rows[0]["s"])


def find_recent_duplicate(owner: str, item: str, merchant: str, amount: float, since_iso: str):
    rows = _rows(
        """SELECT * FROM actions WHERE agent_id = ? AND LOWER(item) = LOWER(?) AND merchant = ?
             AND amount = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1;""",
        (owner, item, merchant, amount, since_iso),
    )
    return rows[0] if rows else None


def get_pending_for_owner(owner: str) -> List[Dict[str, Any]]:
    return _rows(
        """SELECT * FROM actions WHERE agent_id = ? AND status = 'HALTED' AND resolution IS NULL
             AND (expires_at IS NULL OR expires_at > ?) ORDER BY created_at ASC;""",
        (owner, now_iso()),
    )


# ---- writes --------------------------------------------------------------------------

def insert_action(record: Dict[str, Any]) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO actions
               (action_id, session_id, agent_id, item, category, merchant, amount, created_at,
                risk_score, category_severity, anomaly_flags, status, reason, expires_at,
                resolution, resolved_at, resolved_by)
               VALUES (:action_id, :session_id, :agent_id, :item, :category, :merchant, :amount, :created_at,
                       :risk_score, :category_severity, :anomaly_flags, :status, :reason, :expires_at,
                       :resolution, :resolved_at, :resolved_by);""",
            record,
        )
        conn.commit()
    finally:
        conn.close()


def resolve_action_atomic(action_id: str, owner: str, resolution: str) -> bool:
    """
    Single conditional UPDATE: succeeds only if the action belongs to `owner`, is HALTED,
    is still unresolved and has not expired. Two concurrent approvals -> exactly one wins.
    """
    now = now_iso()
    conn = get_connection()
    try:
        cur = conn.execute(
            """UPDATE actions SET resolution = ?, resolved_at = ?, resolved_by = ?
               WHERE action_id = ? AND agent_id = ? AND status = 'HALTED'
                 AND resolution IS NULL AND (expires_at IS NULL OR expires_at > ?);""",
            (resolution, now, owner, action_id, owner, now),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()
