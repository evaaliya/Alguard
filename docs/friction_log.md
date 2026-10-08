cat > FRICTION_LOG.md << 'EOF'
# Friction Log

## 1. Installing the Alexa AI CLI
- **Task attempted:** Install `@alexa-ai/cli` following the official "Set Up Your Development Environment" guide.
- **Steps taken:** Ran `npm install -g @alexa-ai/cli` as documented, on two separate occasions (Sep 15 and Sep 17).
- **Expected result:** Package installs from the public npm registry.
- **Actual result:** `404 Not Found` — the package isn't on public npm; it's hosted in a private AWS CodeArtifact registry gated behind Private Preview partner access, which the docs don't state until several steps deeper into the same guide.
- **Severity:** High — blocks onboarding entirely, with no indication until the failure itself that access is restricted.
- **Workaround:** None available. Confirmed via Amazon support ticket #80330076 and, separately, hackathon organizer comments that participants are not granted this access and are not expected to use it.
- **Actionable suggestion:** State plainly at the top of the setup guide (before any install command) that MCP Toolkit / Category SDK access requires an approved Private Preview partnership, with a link to how to request it.

## 2. Enabling Claude Code via Amazon Bedrock
- **Task attempted:** Authenticate Claude Code through Amazon Bedrock using an AWS account with promotional credits, to avoid a separate Claude subscription.
- **Steps taken:** Enabled Bedrock, attempted `anthropic.claude-sonnet-5` via Claude Code's Bedrock login flow; on failure, tried `anthropic.claude-haiku-4-5` directly in the Bedrock Workbench console (no Claude Code involved) to isolate the cause.
- **Expected result:** At least one Anthropic model accessible, given active AWS credits and prior successful Bedrock use (Amazon Nova) on the same account.
- **Actual result:** Both attempts returned `403 permission_error: model is not available for this account` — confirming the restriction is per-vendor (all Anthropic models), not per-model or per-tool.
- **Severity:** Medium — doesn't block the core project, only IDE convenience.
- **Workaround:** Continued using Amazon Nova models (already accessible) for the project's own Bedrock integration instead of Anthropic models.
- **Actionable suggestion:** Have the Bedrock console distinguish "vendor not enabled on this account" from a generic permission error, and surface a direct "Request access" action for that vendor.

## 3. mcp Python SDK's breaking 2.0.0 release
- **Task attempted:** Run our already-working MCP server and demo client after a routine `pip install -r requirements.txt`.
- **Steps taken:** Re-ran `python -m mcp_server.server` and `python demo/simulate_session.py`, unchanged from a previous working session.
- **Expected result:** Same behavior as before (server starts, client connects over Streamable HTTP).
- **Actual result:** `ImportError: cannot import name 'streamablehttp_client'` — `mcp` had silently resolved to `2.0.0`, which renamed `FastMCP`→`MCPServer` and `streamablehttp_client`→`streamable_http_client` with no compatibility shim and no upper-bound warning in our own unpinned dependency.
- **Severity:** High — broke a previously-working demo with zero code changes on our side.
- **Workaround:** Pinned `mcp[cli]>=1.10,<2.0.0` in `requirements.txt`.
- **Actionable suggestion:** This is exactly the gap we filed against upstream — see our Open Source Mini Challenge contribution (docs/dual-support-1x-2x branch addressing issue #3309): a short "supporting both majors during transition" doc section would have saved us this entire debugging cycle.
