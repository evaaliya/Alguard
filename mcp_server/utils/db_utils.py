"""
Thin sqlite helpers. Every read is scoped to an owner (verified end-user subject);
there is no function here that returns another user's rows. Resolution is a single
atomic UPDATE (no check-then-act race).
"""
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp_server.utils.audit_log import append_audit_event

# Override with ALGUARD_DB_PATH to relocate the Tier 1 actions ledger (e.g. a
# mounted volume). Tier 2 does not write this path directly -- it goes through
# mcp_server/utils/tier2_store.py, the narrow write-only surface.
DB_PATH = Path(os.environ.get("ALGUARD_DB_PATH") or (
    Path(__file__).resolve().parent.parent.parent / "data" / "actions_log.db"
))
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
    _migrate_schema(conn)
    conn.commit()
    conn.close()


def _migrate_schema(conn) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(actions);").fetchall()}
    for col, decl in (
        ("halt_cause", "TEXT"),
        ("fulfillment", "TEXT"),
    ):
        if col not in existing:
            conn.execute(f"ALTER TABLE actions ADD COLUMN {col} {decl};")
    # trusted merchants overlay table
    conn.execute("""CREATE TABLE IF NOT EXISTS user_trusted_merchants (
        owner TEXT NOT NULL, merchant TEXT NOT NULL, added_at TEXT NOT NULL,
        PRIMARY KEY (owner, merchant));""")



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
    """Tier 2 only. Denials cost more than approvals earn.

    Runs inside BEGIN IMMEDIATE so the read-modify-write of trust_score / counters is
    serialized across processes — two concurrent resolutions for the same owner can no
    longer lose one bump."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
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
    except Exception:
        conn.execute("ROLLBACK")
        raise
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

_INSERT_SQL = """INSERT INTO actions
    (action_id, session_id, agent_id, item, category, merchant, amount, created_at,
     risk_score, category_severity, anomaly_flags, status, reason, halt_cause, expires_at,
     resolution, resolved_at, resolved_by)
    VALUES (:action_id, :session_id, :agent_id, :item, :category, :merchant, :amount, :created_at,
            :risk_score, :category_severity, :anomaly_flags, :status, :reason, :halt_cause, :expires_at,
            :resolution, :resolved_at, :resolved_by);"""


def _insert(conn: sqlite3.Connection, record: Dict[str, Any]) -> None:
    conn.execute(_INSERT_SQL, record)


def _audit_attempt(record: Dict[str, Any]) -> None:
    append_audit_event("attempt", record["action_id"], record["agent_id"], {
        "status": record["status"], "item": record["item"],
        "merchant": record["merchant"], "amount": record["amount"],
    })


def insert_action(record: Dict[str, Any]) -> None:
    """Insert one attempt (not atomic vs dedup). Prefer insert_action_if_not_duplicate
    at the Tier-1 boundary, which closes the cross-process read-decide-insert race."""
    conn = get_connection()
    try:
        _insert(conn, record)
        conn.commit()
    finally:
        conn.close()
    _audit_attempt(record)


def insert_action_if_not_duplicate(
    record: Dict[str, Any], owner: str, item: str, merchant: str, amount: float, since_iso: str
) -> Optional[Dict[str, Any]]:
    """Atomically insert a new attempt UNLESS an identical (owner, item, merchant, amount)
    attempt already exists within `since_iso`. The check + insert run in one BEGIN IMMEDIATE
    transaction, which serializes writers across processes (not just threads) — two concurrent
    duplicate attempts resolve to exactly one row. Returns the winning duplicate row if one
    exists, else None (the new row was inserted)."""
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        dup = conn.execute(
            """SELECT * FROM actions WHERE agent_id = ? AND LOWER(item) = LOWER(?) AND merchant = ?
                 AND amount = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1;""",
            (owner, item, merchant, amount, since_iso),
        ).fetchone()
        if dup is not None:
            conn.execute("ROLLBACK")
            return dict(dup)
        _insert(conn, record)
        conn.commit()
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    _audit_attempt(record)
    return None


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
        ok = cur.rowcount == 1
    finally:
        conn.close()
    if ok:
        append_audit_event("resolve", action_id, owner, {"resolution": resolution})
    return ok


def expire_stale_halts() -> int:
    """Roll up any HALTED, still-unresolved action whose approval window has closed:
    mark it EXPIRED so it is terminal in the audit trail and stops accumulating as
    "open". Idempotent; returns the number of rows transitioned."""
    now = now_iso()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT action_id, agent_id FROM actions WHERE status = 'HALTED' AND resolution IS NULL "
            "AND expires_at IS NOT NULL AND expires_at <= ?;",
            (now,),
        ).fetchall()
        cur = conn.execute(
            """UPDATE actions SET resolution = 'EXPIRED', resolved_at = ?, resolved_by = 'system'
               WHERE status = 'HALTED' AND resolution IS NULL
                 AND expires_at IS NOT NULL AND expires_at <= ?;""",
            (now, now),
        )
        conn.commit()
    finally:
        conn.close()
    for r in rows:
        append_audit_event("expire", r["action_id"], r["agent_id"], {})
    return len(rows)


# ---- reconciliation / maintenance ---------------------------------------------------


def reconcile_unfulfilled() -> int:
    """Heal the crash window between resolve and fulfill.

    `apply_resolution.resolve_pending_action` resolves, then fulfills, then receipts — if the
    process dies between resolve and fulfill, the action is APPROVED but never executed and no
    receipt exists. This scans for any action that should have been fulfilled but wasn't, and
    finishes the job. Idempotent: `fulfill()` is a conditional UPDATE and the receipts ledger
    dedupes by action_id. Returns how many rows it healed. Call on startup and/or a schedule.
    """
    rows = _rows(
        """SELECT action_id, amount, status, resolution FROM actions
           WHERE fulfillment IS NULL
             AND (status IN ('OK', 'MONITOR') OR resolution = 'APPROVED');""",
        (),
    )
    healed = 0
    for r in rows:
        from mcp_server.utils.fulfillment import fulfill
        from mcp_server.utils.receipts import send_purchase_receipt
        fulfill(r["action_id"], r["amount"])
        action = get_action(r["action_id"])
        if action is not None and action.get("resolution") == "APPROVED":
            send_purchase_receipt({**action, "status": "APPROVED_BY_USER"})
        healed += 1
    return healed


def maintenance(retention_days: Optional[int] = None) -> Dict[str, int]:
    """One maintenance pass: roll up stale halts, prune terminal actions past retention,
    and checkpoint the WAL. Bounds actions_log.db growth. Returns counts."""
    expired = expire_stale_halts()
    pruned = 0
    days = retention_days if retention_days is not None else int(os.environ.get("ALGUARD_RETENTION_DAYS", "0") or 0)
    if days and days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="microseconds")
        conn = get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM actions WHERE created_at < ? AND status != 'HALTED';",
                (cutoff,),
            )
            conn.commit()
            pruned = cur.rowcount
        finally:
            conn.close()
    return {"expired": expired, "pruned": pruned}


# ---- user trusted merchants (allowlist overlay) ------------------------------------

def list_trusted_merchants(owner: str) -> List[str]:
    rows = _rows("SELECT merchant FROM user_trusted_merchants WHERE owner = ? ORDER BY merchant;", (owner,))
    return [r["merchant"] for r in rows]


def add_trusted_merchant(owner: str, merchant: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_trusted_merchants (owner, merchant, added_at) VALUES (?, ?, ?);",
            (owner, merchant.strip().lower(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def remove_trusted_merchant(owner: str, merchant: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM user_trusted_merchants WHERE owner = ? AND merchant = ?;",
            (owner, merchant.strip().lower()),
        )
        conn.commit()
    finally:
        conn.close()


# ---- user banned merchants (denylist) --------------------------------------------

def list_banned_merchants(owner: str) -> List[str]:
    rows = _rows("SELECT merchant FROM user_banned_merchants WHERE owner = ? ORDER BY merchant;", (owner,))
    return [r["merchant"] for r in rows]


def add_banned_merchant(owner: str, merchant: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO user_banned_merchants (owner, merchant, added_at) VALUES (?, ?, ?);",
            (owner, merchant.strip().lower(), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def remove_banned_merchant(owner: str, merchant: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM user_banned_merchants WHERE owner = ? AND merchant = ?;",
            (owner, merchant.strip().lower()),
        )
        conn.commit()
    finally:
        conn.close()