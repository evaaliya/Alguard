# Product Feedback — Amazon Developer Hackathon

**Project:** Alguard — an Alexa+ Purchase Guard
**Track:** Alexa+
**Mini-challenges:** AWS Builder, Open Source
**Builder:** Diana Aliya 

---

## Alexa+ MCP Toolkit

**What I used it for:** building the entire Tier-1 agent channel — a
Streamable HTTP MCP server (`mcp_server/server.py`) that exposes exactly
three tools to Alexa+: `attempt_purchase`, `get_session_status`,
`get_audit_trail`. Design followed the Alexa+ MCP QuickStart Guide and the
MCP Design Guide for Alexa+ end to end.

**What worked well:**
- The spec 2025-11-25 requirement of Streamable HTTP is a clear, single
  technical constraint — no ambiguity about which transport to build.
- `addon.json` schema from the QuickStart Guide is small and machine-checkable.
- The Design Guide's "Tools, Schema, and Data Design" section is the single
  most useful document in the whole toolkit. The "declare only what you
  honor" rule directly changed our code (we removed an `agent_id` tool
  parameter — see below).

**What needs work:**
- **Authentication checklist vs. SDK reality.** The Alexa+ MCP
  Authentication checklist says `WWW-Authenticate` on 401 responses is
  "Not Supported Yet" on Alexa's side, but the official MCP Python SDK
  adds that header by default. I've flagged this in `auth_context.py`
  rather than shipped a custom middleware to strip it — but a builder
  without Private Preview access can't actually test which behavior
  Alexa+ expects. This gap between published requirement and SDK default
  is a real blocker for certification.
- **Web Simulator access is the bottleneck.** The only certification-grade
  integration test is "Test in the Web Simulator" — and getting access to
  it is not something an outside builder can do in a weekend. Without it,
  a first-time builder is guessing at half the checklist.
- **Model access page churn.** The AWS Bedrock "Model access" page was
  retired mid-build. For a while we couldn't tell whether a
  `ValidationException: Operation not allowed` was an IAM problem, a
  region problem, a model-availability problem, or an account-level hold
  (it was the last one — see AWS Builder section). Clearer error text
  would have saved hours.

**Onboarding feel:** N/A — CLI gated behind Private Preview, see FRICTION_LOG.md #1

**Would I build with it again:** yes — but only with Private Preview
access confirmed before the hackathon starts.

---

## Amazon Bedrock (AWS Builder mini-challenge)

**What I used it for:** generating the customer-facing explanation for a
HALTED purchase attempt, in `mcp_server/utils/bedrock_explain.py`. The
explanation is the one place where the risk engine's internal vocabulary
("risk_score >= threshold, severity=High, flags=[retry_after_halt]") must
be translated into a calm sentence a human will actually read. Bedrock is
called through the `converse` API, on a **non-critical path** — a Bedrock
failure must never change the risk decision itself, only the wording.
Fallback to a deterministic template is in `explain.py`.

**What worked well:**
- `converse` is the right API. It removes the whole
  `anthropic_version` / `contentType` / `accept` ceremony of
  `invoke_model`, and behaves identically across model families.
- `list_foundation_models` returns `modelLifecycle.status` — being able
  to filter `ACTIVE` vs `LEGACY` before writing a single line of calling
  code saved real time.
- The `bedrock-runtime` client picks up `~/.aws/credentials` and
  environment variables exactly as advertised.

**What needs work:**
- **Account-level authorization hold on new accounts.** On a fresh AWS
  account with no billing history, every `converse` call to every model —
  Anthropic, DeepSeek, Nova — returns
  `ValidationException: Operation not allowed`, **even after** the model
  shows up as `ACTIVE` in `list_foundation_models` and **even after**
  "Model access" checkboxes are set. This is the single most confusing
  error text in the whole stack: it looks like an IAM problem, it is not
  an IAM problem, and there is no console surface that says "waiting for
  billing history". A distinct error code (e.g.
  `AccountNotYetAuthorizedForBedrock`) would have saved a day.
- **"Model access" page retirement.** Removing the page while leaving
  `ValidationException` as the failure mode made it impossible to tell
  whether a model was genuinely gated or the account was gated.
- **Region availability documentation.** The same `modelId` string
  returned different `ACTIVE` / `LEGACY` / missing states across
  `us-east-1` and `us-west-2`; a per-region matrix in the docs would help.

**Which AWS services:** Amazon Bedrock (`bedrock-runtime`, `converse`
API). No other AWS services were called in the Golden Path — SQLite
holds all state, and the tier-2 approval channel is a local FastAPI
service by design (see the README's security model).

**Onboarding feel:** 3/5. The API itself is clean; the account-gating
error is what costs it two points.

**Would I build with it again:** yes, on an account with established
billing history. Not on a fresh hackathon account without a pre-flight
check first.

---

## Feature requests

**Critical — MCP server session resumption across reconnects.**
The Streamable HTTP spec describes `Last-Event-ID` resumption, but
neither the Python SDK's `streamablehttp_client` nor `FastMCP` expose it
as a first-class feature in the version this project pins
(`mcp[cli]<2.0.0`). For a purchase-guard use case, a dropped SSE stream
during a HALTED → APPROVED transition is exactly when resumption matters
most. This is on the roadmap, but it's the feature I'd pay for.

**Important — a first-class "human approval" primitive.**
Every agentic-commerce system ends up building the same shape: the agent
can *request* a high-consequence action, but only a human on a **separate
authenticated channel** can *approve* it. Today every builder rolls this
themselves (Tier 2 in this project is a whole FastAPI service). A
documented Alexa+ pattern for "approval outside the conversation" — with
the account-linking model already specified — would let builders stop
re-inventing it.

**Nice-to-have — Bedrock Guardrails on tool descriptions.**
When Alexa+ picks a tool, it's reading the tool description. A builder
who writes a description that overpromises (e.g. "this will complete a
purchase") gets confidently wrong behavior from the model. A Guardrail
that flags descriptions which don't match the tool's actual behavior
would catch the exact class of bug the Alexa+ Design Guide warns about.

---

## Friction log

| # | Task attempted | Steps taken | Expected | Actual | Severity | Workaround | Suggestion |
|---|---|---|---|---|---|---|---|
| 1 | Confirm Bedrock is callable from a fresh account | `list_foundation_models` → pick Claude Haiku → `converse` | One-line code call | `ValidationException: Operation not allowed` | Critical | Switched provider (DeepSeek, then Nova) — same result | Distinct error code for account-level hold, and a console banner |
| 2 | Set up OAuth 2.1 resource-server auth | Configured `AuthSettings` per SDK docs | 401 with `WWW-Authenticate` on missing token | SDK adds header; Alexa+ Authentication checklist says header is "Not Supported Yet" | Important | None — flagged in `auth_context.py`, not fixed | Reconcile SDK default with published Alexa+ requirement |
| 3 | Test the real integration path | Read QuickStart Guide → `alexa-ai configure` → tunnel → `alexa-ai deploy` → "Test in the Web Simulator" | Simulator opens, tools listed | Simulator access requires Private Preview; not available to outside builders | Critical | Used `demo/simulate_session.py` as a generic MCP client instead | A public read-only simulator would eliminate most first-time-builder uncertainty |
| 4 | Create `requirements.txt` via `echo >>` while VS Code had it open | `echo "boto3>=1.34" >> requirements.txt` | File updated | VS Code buffered a stale version, later overwrote the append; `cat` and VS Code disagreed | Minor | Closed VS Code before every terminal edit | (Editor problem, not AWS — noting it because it cost time) |

---

## What I'd build with more time

- **Wire `resolve_pending_action` to a real charge.** Today the Tier-2
  approval returns `APPROVED_BY_USER` and writes a receipt marked
  `simulated: true`. The shape is right (`ChargePermissionId` /
  partner-wallet checkout), the last mile isn't.
- **Replace the text-heuristic category inference with MCC data** from
  the payment rail. `category_risk.py` is honest about this: it's a
  heuristic, and a real deployment needs the merchant category code.
- **Split the SQLite file across the two tiers.** Right now Tier 1 and
  Tier 2 share `actions_log.db`. The `resolve_action_atomic` UPDATE is
  correct across processes, but the deployment shape should be "Tier 2
  behind a write-only API", not "two processes, one file".