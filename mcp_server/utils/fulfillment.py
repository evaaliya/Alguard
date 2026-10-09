"""
Post-decision fulfillment on a SIMULATED payment rail.

Real Checkout integration (ChargePermissionId / partner wallet) is a TODO
below; this module ships a deterministic simulated rail so the demo can show
the loop closing end-to-end: decision → human approve → fulfillment → status.

The API is designed so a real rail can be dropped in without touching callers:
just implement PaymentRail and swap the instance in purchase_flow.py and
apply_resolution.py.
"""
from dataclasses import dataclass
from typing import Protocol

from mcp_server.utils.db_utils import get_connection, now_iso


@dataclass(frozen=True)
class FulfillmentResult:
    ok: bool
    fulfillment: str      # 'COMPLETED_SIMULATED' | 'FAILED'
    simulated: bool = True


class PaymentRail(Protocol):
    """Minimal interface a real payment rail must implement."""
    def charge(self, action_id: str, amount: float) -> FulfillmentResult: ...


class SimulatedRail:
    """Records fulfillment without charging anything. Always succeeds."""

    def charge(self, action_id: str, amount: float) -> FulfillmentResult:
        # TODO(checkout): real Alexa+ Checkout / ChargePermissionId call goes here.
        return FulfillmentResult(ok=True, fulfillment="COMPLETED_SIMULATED", simulated=True)


# Module-level default. Swap for a real rail in one place.
DEFAULT_RAIL: PaymentRail = SimulatedRail()


def fulfill(action_id: str, amount: float, rail: PaymentRail | None = None) -> FulfillmentResult:
    """
    Idempotent: if a row already has a fulfillment, return it unchanged.
    Uses a conditional UPDATE so concurrent callers cannot double-fulfill.
    """
    rail = rail or DEFAULT_RAIL
    result = rail.charge(action_id, amount)

    conn = get_connection()
    try:
        cur = conn.execute(
            """UPDATE actions SET fulfillment = ?
               WHERE action_id = ? AND fulfillment IS NULL;""",
            (result.fulfillment, action_id),
        )
        conn.commit()
        if cur.rowcount == 1:
            return result
        # Already fulfilled — return whatever is stored.
        row = conn.execute(
            "SELECT fulfillment FROM actions WHERE action_id = ?;", (action_id,)
        ).fetchone()
        return FulfillmentResult(
            ok=True,
            fulfillment=row["fulfillment"] if row else result.fulfillment,
            simulated=True,
        )
    finally:
        conn.close()
