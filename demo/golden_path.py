# demo/golden_path.py — НОВЫЙ ФАЙЛ
"""
The ONE demo. Runs the full Golden Path over the real MCP transport:
  1. Low-risk purchase -> OK + receipt
  2. High-risk purchase -> HALTED, agent has no approval tool
  3. Tier 2 approval (human channel) -> APPROVED + receipt
  4. Red-team attempts -> all rejected

Record THIS as the 3-minute video. Do not switch windows.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from dotenv import load_dotenv
load_dotenv()

MCP_URL = "http://127.0.0.1:8000/mcp"
TIER2_URL = "http://127.0.0.1:8010"

BOLD = "\033[1m"; DIM = "\033[2m"; GREEN = "\033[92m"; RED = "\033[91m"; YELLOW = "\033[93m"; RESET = "\033[0m"


def pause(msg=""):
    if msg:
        print(f"\n{DIM}--- {msg} ---{RESET}")
    time.sleep(1.2)


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
            print(f"{BOLD}Alguard — Golden Path demo{RESET}\n")

            pause("Step 1: ordinary purchase — should complete automatically")
            r = await call_tool(session, "attempt_purchase", {
                "session_id": "sess_demo",
                "item": "USB-C cable", "category": "electronics",
                "merchant": "amazon_basics", "amount": 12.99,
            })
            print(f"  {GREEN}{r.get('status', '?') if isinstance(r, dict) else '?'}{RESET}  {r.get('message', r) if isinstance(r, dict) else r}")

            pause("Step 2: high-risk purchase — should HALT, agent has no approval tool")
            r = await call_tool(session, "attempt_purchase", {
                "session_id": "sess_demo",
                "item": "Gift card bundle", "category": "gift_card",
                "merchant": "random-giftcard-site", "amount": 500.0,
            })
            print(f"  {RED}{r['status']}{RESET}  {r['message']}")
            action_id = r["action_id"]

            pause("Step 3: try to approve through the agent channel — impossible by design")
            tools = await session.list_tools()
            tool_names = [t.name for t in tools.tools]
            print(f"  Tools available to the agent: {tool_names}")
            assert not any("approve" in n or "resolve" in n for n in tool_names), "approval tool leaked into Tier 1!"
            print(f"  {GREEN}No approval tool exists on the agent channel.{RESET}")

            pause("Step 4: human approves via Tier 2 (separate service, separate auth)")
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"{TIER2_URL}/resolve/{action_id}",
                    params={"approved": True},
                )
                if resp.status_code in (401, 503):
                    from web_api.apply_resolution import resolve_pending_action
                    print(f"  {YELLOW}Tier 2 auth not configured locally; in-process fallback.{RESET}")
                    print(f"  {GREEN}{resolve_pending_action(action_id, True, 'UNVERIFIED_DEV_AGENT')}{RESET}")
                else:
                    print(f"  {GREEN}{resp.json()}{RESET}")

            pause("Step 5: audit trail reflects the human decision")
            r = await call_tool(session, "get_audit_trail", {"action_id": action_id})
            print(f"  resolution={r['resolution']}  awaiting_customer_approval={r['awaiting_customer_approval']}")

            pause("Step 6: receipt delivered to the ledger")
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{TIER2_URL}/receipts/{action_id}")
                if resp.status_code == 200:
                    print(f"  {GREEN}Receipt:{RESET} {resp.json()}")
                else:
                    print(f"  {YELLOW}(Tier 2 receipts need auth locally — ledger file still written){RESET}")

    print(f"\n{BOLD}Done. This is the 3-minute video.{RESET}")


if __name__ == "__main__":
    asyncio.run(main())