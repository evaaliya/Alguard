# Friction Log

## 1. Installing the Alexa AI CLI
- **Task attempted:** Install `@alexa-ai/cli` following the official "Set Up Your Development Environment" guide.
- **Steps taken:** Ran `npm install -g @alexa-ai/cli` as documented, on two separate occasions (Sep 15 and Sep 17).
- **Expected result:** Package installs from the public npm registry.
- **Actual result:** `404 Not Found` — the package isn't on public npm; it's hosted in a private AWS CodeArtifact registry gated behind Private Preview partner access, which the docs don't state until several steps deeper into the same guide.
- **Severity:** High — blocks onboarding entirely, with no indication until the failure itself that access is restricted.
- **Workaround:** None available. Confirmed via Amazon support ticket #80330076 and, separately, hackathon organizer comments that participants are not granted this access and are not expected to use it.
- **Actionable suggestion:** State plainly at the top of the setup guide (before any install command) that MCP Toolkit / Category SDK access requires an approved Private Preview partnership, with a link to how to request it.

## 2. Amazon Bedrock model availability on a fresh account
- **Task attempted:** Generate the customer-facing explanation for a HALTED purchase via Amazon Bedrock (`mcp_server/utils/bedrock_explain.py`), using `boto3`'s `converse` API.
- **Steps taken:** Enabled Bedrock, then attempted — in order — `anthropic.claude-sonnet-5` (via Claude Code's Bedrock login flow), `anthropic.claude-haiku-4-5` (directly via the Workbench), `deepseek.v3.2`, and `amazon.nova-lite-v1:0`.
- **Expected result:** At least one model accessible, given active AWS credits.
- **Actual result:** Two distinct failures, both account-level, neither fixable by IAM policy or by Model access state:
  - Anthropic models: `403 permission_error: model is not available for this account` — a per-vendor restriction, not per-model.
  - DeepSeek and Nova: `ValidationException: Operation not allowed` on `converse` — same error text as a malformed request, but reproduced on every model in the region.
- **Severity:** Medium — doesn't block the core project. `mcp_server/utils/explain.py` falls back to a deterministic template; `demo/golden_path.py` Step 2 exercises that fallback.
- **Workaround:** Kept the Bedrock call site in `bedrock_explain.py` with a working fallback; the integration is documented and the API call is in the repo, ready to run on an account where Bedrock is authorized.
- **Actionable suggestion:** Distinguish "vendor not enabled on this account" and "account not yet authorized for Bedrock" from a generic validation error, and surface a direct "Request access" action in the console.

## 3. mcp Python SDK's breaking 2.0.0 release
- **Task attempted:** Run our already-working MCP server and demo client after a routine `pip install -r requirements.txt`.
- **Steps taken:** Re-ran `python -m mcp_server.server` and `python demo/simulate_session.py`, unchanged from a previous working session.
- **Expected result:** Same behavior as before (server starts, client connects over Streamable HTTP).
- **Actual result:** `ImportError: cannot import name 'streamablehttp_client'` — `mcp` had silently resolved to `2.0.0`, which renamed `FastMCP`→`MCPServer` and `streamablehttp_client`→`streamable_http_client` with no compatibility shim and no upper-bound warning in our own unpinned dependency.
- **Severity:** High — broke a previously-working demo with zero code changes on our side.
- **Workaround:** Pinned `mcp[cli]>=1.10,<2.0.0` in `requirements.txt`.
- **Actionable suggestion:** This is exactly the gap we filed against upstream — see our Open Source Mini Challenge contribution (fork branch `docs/dual-support-1x-2x` addressing issue #3309): a short "supporting both majors during transition" doc section would have saved us this entire debugging cycle.