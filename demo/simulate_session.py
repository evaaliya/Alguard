"""
End-to-end demo over the REAL transport (Streamable HTTP), proving this is an actual
MCP integration and not just a mention in the README.

Note: this script plays the role of "generic MCP client", standing in for Alexa+ itself
during local development. It deliberately does NOT pass an agent_id/caller-identity
argument to attempt_purchase -- that parameter was removed from the tool schema on
purpose (see mcp_server/server.py + mcp_server/utils/auth_context.py): caller identity
must come from the authenticated request context, not from something the calling model
fills in. Before submitting for certification, the actual integration test is "Test in
the Web Simulator" per the Alexa+ MCP QuickStart Guide, not this script.

Run:
  1) terminal 1: python -m mcp_server.server        # starts the Tier 1 MCP server
  2) terminal 2: python -m web_api.resolve_action    # starts the Tier 2 resolution API
  3) terminal 3: python demo/simulate_session.py     # plays the scenario below

Local dev only: start Tier 2 with  ALGUARD_DEV_USER=UNVERIFIED_DEV_AGENT  (the same identity Tier 1 uses
when OAuth is off). With real OAuth, Tier 2 requires a Bearer token and there is no dev user.

Requires: pip install "mcp[cli]<2.0.0" (see requirements.txt -- mcp 2.0.0 renamed
FastMCP to MCPServer and streamablehttp_client to streamable_http_client with a
different signature; pinning <2.0.0 keeps this script and mcp_server/server.py valid
as written). Default FastMCP streamable-http endpoint is http://127.0.0.1:8000/mcp.
"""
import asyncio
import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "http://127.0.0.1:8000/mcp"
RESOLVE_API_URL = "http://127.0.0.1:8010"

SESSION_ID = "sess_alexa_demo"
# No AGENT_ID constant: caller identity is resolved server-side from the request
# context (see mcp_server/utils/auth_context.py), never supplied by the client.


async def call_tool(session: ClientSession, name: str, args: dict):
    result = await session.call_tool(name, args)
    # FastMCP tools returning dicts come back as structured content
    for block in result.content:
        if hasattr(block, "text"):
            try:
                return json.loads(block.text)
            except json.JSONDecodeError:
                return block.text
    return result


async def main():
    async with streamablehttp_client(MCP_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            print("=== Step 1: ordinary low-risk purchase ===")
            r1 = await call_tool(session, "attempt_purchase", {
                "session_id": SESSION_ID,
                "item": "USB-C cable", "category": "electronics",
                "merchant": "amazon_basics", "amount": 12.99,
            })
            print(r1)

            print("\n=== Step 2: gift_card on a clean history -> expect HALTED (High category is a hard rule) ===")
            r2 = await call_tool(session, "attempt_purchase", {
                "session_id": SESSION_ID,
                "item": "Gift card bundle", "category": "gift_card",
                "merchant": "random-giftcard-site", "amount": 500.0,
            })
            print(r2)
            action_id = r2["action_id"]

            print("\n=== Step 3: ordinary Low purchase while the halt is open -> expect MONITOR ===")
            r3 = await call_tool(session, "attempt_purchase", {
                "session_id": SESSION_ID,
                "item": "Coffee", "category": "subscription",
                "merchant": "amazon_basics", "amount": 6.50,
            })
            print(r3)

            print("\n=== Note: the agent has NO tool to resolve the HALTED action. ===")
            print("Resolving it now via the separate Tier 2 endpoint (simulating the Alexa app):")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{RESOLVE_API_URL}/resolve/{action_id}",
            params={"approved": True},
        )
        if resp.status_code in (401, 503):
            # Demo fallback: Tier 2 auth not configured locally. Call the same
            # resolution function the HTTP layer would call — same code path,
            # no auth. Production uses the HTTP endpoint with a Bearer token.
            print(f"  (Tier 2 returned {resp.status_code}; falling back to in-process resolution for demo)")
            from mcp_server.utils.db_utils import ensure_schema
            from web_api.apply_resolution import resolve_pending_action
            ensure_schema()
            print(" ", resolve_pending_action(action_id, True, "UNVERIFIED_DEV_AGENT"))
        else:
            print(resp.status_code, resp.json())


if __name__ == "__main__":
    asyncio.run(main())
