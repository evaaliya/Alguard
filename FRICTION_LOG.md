# Friction Log

## 1. Installing the Alexa AI CLI
- **Task attempted:** Install `@alexa-ai/cli` following the official "Set Up Your Development Environment" guide.
- **Steps taken:** Ran `npm install -g @alexa-ai/cli` as documented, on two separate occasions.
- **Expected result:** Package installs from the public npm registry.
- **Actual result:** `404 Not Found` — the package isn't on public npm; it's hosted in a private AWS CodeArtifact registry gated behind Private Preview partner access, which the docs don't state until several steps deeper into the same guide.
- **Severity:** High — blocks onboarding entirely.
- **Workaround:** None. Confirmed via Amazon support and hackathon organizer comments that participants are not granted this access.
- **Actionable suggestion:** State plainly at the top of the setup guide (before any install command) that MCP Toolkit / Category SDK access requires an approved Private Preview partnership.

## 2. Enabling Claude Code via Amazon Bedrock
- **Task attempted:** Authenticate Claude Code through Amazon Bedrock using an AWS account with promotional credits.
- **Steps taken:** Enabled Bedrock, attempted `anthropic.claude-sonnet-5`; on failure, tried `anthropic.claude-haiku-4-5`, then `deepseek.v3.2`, then `amazon.nova-lite-v1:0` — all via `converse`.
- **Expected result:** At least one model accessible, given active AWS credits.
- **Actual result:** Every model returned `ValidationException: Operation not allowed`, regardless of IAM policy or Model access state — an account-level authorization hold.
- **Severity:** Medium — doesn't block the core project; the fallback template in `explain.py` covers the customer-facing explanation.
- **Workaround:** Kept the Bedrock call site in `bedrock_explain.py`; exercised the deterministic fallback in `demo/golden_path.py` Step 2.
- **Actionable suggestion:** Distinguish "account not yet authorized for Bedrock" from a generic validation error, and surface a direct "Request access" action.

## 3. mcp Python SDK's breaking 2.0.0 release
- **Task attempted:** Run our already-working MCP server and demo client after `pip install -r requirements.txt`.
- **Steps taken:** Re-ran `python -m mcp_server.server` and `demo/simulate_session.py`, unchanged.
- **Expected result:** Same behavior as before.
- **Actual result:** `ImportError: cannot import name 'streamablehttp_client'` — `mcp` resolved to `2.0.0`, which renamed `FastMCP`→`MCPServer` and `streamablehttp_client`→`streamable_http_client` with no compatibility shim.
- **Severity:** High — broke a previously-working demo with zero code changes on our side.
- **Workaround:** Pinned `mcp[cli]>=1.10,<2.0.0` in `requirements.txt`.
- **Actionable suggestion:** A short "supporting both majors during transition" doc section would have saved us this entire debugging cycle.
