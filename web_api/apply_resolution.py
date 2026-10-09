"""
Not an MCP tool. Called only by web_api/resolve_action.py after the END USER is
authenticated. Ownership is enforced in the data layer: get_action(owner=...) makes a
foreign action look like a missing one, and the UPDATE is conditional + atomic.
"""
from typing import Any, Dict

from mcp_server.utils.db_utils import get_action, resolve_action_atomic, bump_agent_trust
from mcp_server.utils.receipts import send_purchase_receipt


def resolve_pending_action(action_id: str, approved: bool, user_id: str) -> Dict[str, Any]:
    action = get_action(action_id, owner=user_id)
    if not action:
        return {"error": "action not found"}
    if action["status"] != "HALTED":
        return {"error": "nothing to resolve"}

    resolution = "APPROVED" if approved else "DENIED"
    # one atomic statement: still unresolved, not expired, owned by user_id
    if not resolve_action_atomic(action_id, user_id, resolution):
        return {"error": "already resolved or expired"}

    bump_agent_trust(user_id, approved)   # only after the atomic win -> no double bump

    if approved:
        # Fulfillment on the simulated rail. The real Checkout call goes inside
        # the PaymentRail implementation, never from the agent channel.
        from mcp_server.utils.fulfillment import fulfill
        fulfill(action_id, action["amount"])
        send_purchase_receipt({**action, "status": "APPROVED_BY_USER"})

    return {"action_id": action_id, "resolution": resolution}
