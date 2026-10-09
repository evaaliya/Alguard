"""Narrow data access for the Tier 2 (human-approval) service.

Tier 1 (mcp_server/server.py) is the sole read-write owner of the actions ledger.
Tier 2 must not import the general db_utils write helpers (insert_action, reset,
bump_agent_trust, ...) — a compromised Tier 2 process would then be able to write
arbitrary rows. Instead Tier 2 gets exactly three operations through this store:

  * pending(owner)            — read-only list of the owner's open HALTED actions
  * receipt(action_id, owner) — read-only receipt lookup
  * resolve(action_id, owner, approved) — the ONE allowed write (atomic, owner-scoped)

Reads run over a `query_only` SQLite connection so even the read path cannot mutate
the ledger. The single write reuses the atomic resolution logic already implemented
in web_api/apply_resolution.py rather than reimplementing it.

Remaining production step (see README "What is still NOT solved"): move Tier 2 into
its own process with a write-only HTTP API so it no longer shares the SQLite file
with Tier 1 at all.
"""
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp_server.utils import db_utils
from mcp_server.utils.receipts import get_receipt


def _readonly_connection(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON;")  # any write on this connection raises
    return conn


class Tier2Store:
    """The only data access Tier 2 is allowed to perform."""

    @staticmethod
    def _db_path() -> Path:
        # Resolve at call time so a test monkeypatch of db_utils.DB_PATH applies,
        # and a runtime ALGUARD_DB_PATH override is picked up without a restart.
        return db_utils.DB_PATH

    def pending(self, owner: str) -> List[Dict[str, Any]]:
        path = self._db_path()
        if not path.exists():
            return []
        conn = _readonly_connection(path)
        try:
            rows = conn.execute(
                "SELECT * FROM actions WHERE agent_id = ? AND status = 'HALTED' "
                "AND resolution IS NULL AND (expires_at IS NULL OR expires_at > ?) "
                "ORDER BY created_at ASC;",
                (owner, db_utils.now_iso()),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def receipt(self, action_id: str, owner: str) -> Optional[Dict[str, Any]]:
        return get_receipt(action_id, owner)

    def resolve(self, action_id: str, approved: bool, owner: str) -> Dict[str, Any]:
        # The one and only write Tier 2 can make (atomic, owner-scoped).
        from web_api.apply_resolution import resolve_pending_action
        return resolve_pending_action(action_id, approved, owner)
