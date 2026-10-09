"""
2.4-B — deterministic agent demo (no LLM key required).

Runs the same 4-step scenario the video shows:
  1. Legitimate purchase: coffee at a known, human-approved merchant → OK + fulfillment.
  2. Agent reads a "poisoned" page and, following its instructions, tries to
     buy a $500 gift card → HALTED, no approval tool exists on the agent channel.
  3. Human reviews in Tier 2 (separate port/process) and DENIES.
  4. Agent asks get_session_status and sees resolution=DENIED.

If ANTHROPIC_API_KEY is set, a Mode A (real LLM tool-calling) variant can
be added later; this script is Mode B only and fully deterministic.

Run:
  terminal 1: python -m mcp_server.server
  terminal 2: ALGUARD_DEV_USER=UNVERIFIED_DEV_AGENT python -m web_api.resolve_action
  terminal 3: python demo/agent_demo.py
"""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_URL = "http://127.0.0.1:8000/mcp"
TIER2_URL = "http://127.0.0.1:8010"
OWNER = "UNVERIFIED_DEV_AGENT"
SESSION_ID = "sess_agent_demo"

BOLD = "\033[1m"; DIM = "\033[2m"
GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"; RESET = "\033[0m"


def step(n, text):
    print(f"\n{BOLD}STEP {n}{RESET}  {DIM}{text}{RESET}")
    time.sleep(0.8)


async def call_tool(session, name, args):
    result = await session.call_tool(name, args)
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

            # ---- STEP 1: legitimate purchase, needs human trust first ----
            step(1, "Legitimate purchase — first-time merchant must be approved by a human")
            r1 = await call_tool(session, "attempt_purchase", {
                "session_id": SESSION_ID,
                "item": "Coffee", "category": "subscription",
                "merchant": "corner-cafe", "amount": 4.50,
            })
            print(f"  {GREEN}{r1['status']}{RESET}  {r1['message']}")
            if r1["status"] == "HALTED":
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{TIER2_URL}/resolve/{r1['action_id']}",
                        params={"approved": True},
                    )
                    print(f"  {DIM}human approved the halt (Tier 2){RESET}  → {resp.status_code}")

            # second attempt at the same merchant is now OK
            r1b = await call_tool(session, "attempt_purchase", {
                "session_id": SESSION_ID,
                "item": "Croissant", "category": "dining",
                "merchant": "corner-cafe", "amount": 3.00,
            })
            print(f"  {GREEN}{r1b['status']}{RESET}  {r1b['message']}")

            # ---- STEP 2: poisoned page drives the agent to attempt a risky purchase ----
            step(2, "Agent reads a poisoned page and tries to buy a $500 gift card")
            poisoned = (Path(__file__).parent / "poisoned_page.html").read_text()
            # In Mode B we don't parse it with an LLM — the payload's intent is
            # pre-extracted, and the agent "follows" it. That's the whole point:
            # the *server* is what stops it, not the agent's good judgment.
            print(f"  {DIM}poisoned page loaded ({len(poisoned)} bytes){RESET}")
            r2 = await call_tool(session, "attempt_purchase", {
                "session_id": SESSION_ID,
                "item": "Steam wallet code", "category": "other",
                "merchant": "steam-deals-now", "amount": 500.0,
            })
            print(f"  {RED}{r2['status']}{RESET}  {r2['message']}")
            action_id = r2["action_id"]

            # ---- STEP 3: the agent has no approval tool ----
            step(3, "Agent has no approval tool — the system that requests is not the system that approves")
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            print(f"  tools: {names}")
            assert not any("approve" in n or "resolve" in n for n in names)
            print(f"  {GREEN}No approve/deny tool exists on the agent channel.{RESET}")

            # ---- STEP 4: human denies in Tier 2; agent reads the result ----
            step(4, "Human DENIES via Tier 2 (separate service); agent reads the audit trail")
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{TIER2_URL}/resolve/{action_id}",
                    params={"approved": False},
                )
                if resp.status_code == 200:
                    print(f"  {YELLOW}human denied (Tier 2){RESET}  {resp.json()}")
                else:
                    from web_api.apply_resolution import resolve_pending_action
                    print(f"  {YELLOW}Tier2 not reachable; in-process fallback{RESET}  "
                          f"{resolve_pending_action(action_id, False, OWNER)}")

            audit = await call_tool(session, "get_audit_trail", {"action_id": action_id})
            print(f"  {RED}agent sees: resolution={audit['resolution']}, "
                  f"awaiting_customer_approval={audit['awaiting_customer_approval']}{RESET}")

    print(f"\n{BOLD}Done — the injection failed, the human decided, the agent can only report it.{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
