# Alguard — Architecture & Workflow Specification

> Post-audit revision. Every fix below corresponds to a specific finding from the
> security review; the "Fixed by" pointers name the exact code path.

## 1. Tier separation (the core invariant)

> **The system that requests a risky action is never the system that approves it.**

| Surface | Port | Role | Authentication | Approve tool? |
|---|---|---|---|---|
| Tier 1 — MCP server | 8000 | Agent channel (Alexa+). `attempt_purchase`, `get_session_status`, `get_audit_trail` | OAuth 2.1 Bearer (resource server), fail-closed | **No** |
| Tier 2 — resolution API | 8010 | Human channel. `/pending`, `/resolve/{id}`, `/receipts/{id}`, trusted/banned merchants | Own audience + `approve` scope, fail-closed | Yes |
| Internal ledger API | 8015 | Tier 2's ONLY route to the actions ledger | Shared secret `ALGUARD_LEDGER_API_TOKEN` (loopback-bound) | Yes (delegated) |
| Demo UI backend | 8020 | Video-only demo UI | None — refuses to start without `ALGUARD_DEMO_UI=1` | Demo-only |

Tier 2 never opens the SQLite file: it goes through the HTTP ledger API via
`mcp_server/utils/tier2_store.py`. The agent channel has no code path, tool, or network
route that can approve a HALTED action.

## 2. Purchase workflow (normative spec)

```
attempt_purchase(owner, session_id, item, category, merchant, amount)
 1. validate   — reject non-finite/<=0/>$1M amount, empty item/merchant, unknown category
 2. rate-limit — per-owner token bucket (ALGUARD_RATE_LIMIT_PER_MIN); raises a clean error
 3. expire     — roll up stale HALTed actions whose approval window closed
 4. dedup      — PREVENT, not detect: find_recent_duplicate() fast path + atomic insert
 5. decide     — risk engine (severity + anomalies + trust) + circuit breaker → OK/MONITOR/HALTED
 6. persist    — insert_action_if_not_duplicate() in one BEGIN IMMEDIATE transaction
 7. fulfill    — OK/MONITOR: fulfill() + send_purchase_receipt()  (HALTED: nothing executes)
 8. respond    — minimal payload to the agent (no thresholds/flags → no oracle)
```

```
HALTED → action_id returned, expires in 30 min
  human sees it on Tier 2 → resolve_pending_action(APPROVE|DENY)
    resolve_action_atomic(): single conditional UPDATE WHERE resolution IS NULL
                            AND (expires_at IS NULL OR expires_at > now) AND agent_id = owner
    → exactly one concurrent resolver wins (rowcount == 1), the other gets 409
  APPROVE → fulfill() + receipt   DENY → stays blocked
```

## 3. Failure modes and how each is closed

| # | Failure mode | Consequence | Fix |
|---|---|---|---|
| F1 | Two concurrent approvals race | double-approve / double-fulfill | `resolve_action_atomic()` conditional UPDATE + `rowcount==1`; `fulfill()` is also a conditional UPDATE |
| F2 | Crash between resolve and fulfill | APPROVED but never executed, no receipt | `reconcile_unfulfilled()` on startup heals it (idempotent) |
| F3 | Two concurrent identical attempts (cross-process) | duplicate rows | `insert_action_if_not_duplicate()` runs check+insert in `BEGIN IMMEDIATE` |
| F4 | Two concurrent trust bumps | lost update on trust_score | `bump_agent_trust()` wrapped in `BEGIN IMMEDIATE` |
| F5 | Ledger API reachable by a third party | IDOR/auth-bypass approve | `ALGUARD_LEDGER_API_TOKEN` shared secret + loopback bind |
| F6 | Demo UI reachable in prod | unauthenticated approve | `demo_server` refuses to start without `ALGUARD_DEMO_UI=1` |
| F7 | Network drops mid-approve (Tier 2 → ledger) | human sees 500, state unknown | `Tier2Store` retries w/ backoff; atomic resolve makes retry idempotent |
| F8 | actions_log.db / JSONL grows unbounded | disk exhaustion → 500 | `maintenance()`: expire stale halts + prune terminal actions past `ALGUARD_RETENTION_DAYS` |
| F9 | Hostile agent floods attempt_purchase | DoS / oracle probing | per-owner rate limiter |
| F10 | Token minted by a rogue OAuth client | cross-channel approve | optional `OAUTH_ALLOWED_CLIENTS` (azp/client_id) allowlist |
| F11 | Receipt double-write on retry | duplicate receipts | receipts ledger dedupes by `action_id` |
| F12 | Bedrock explanation provenance | ambiguous which engine wrote the copy | explicit honest `source` = `bedrock-converse` \| `local-fallback` |

## 4. Data model (unchanged, owner-scoped)

- `actions` — one row per attempt; `agent_id` = verified OAuth `sub` (owner). Every read
  is `WHERE agent_id = ?`; a foreign action is indistinguishable from a missing one.
- `agent_trust` — per-owner trust score, serialized updates.
- `user_trusted_merchants` / `user_banned_merchants` — owner-scoped overlays.
- `audit_log.jsonl` — append-only; `receipts_ledger.jsonl` — idempotent by action_id.

## 5. Logging / observability contract

- Tier 1 logs `action / status / owner / dup / amount` per attempt (never the reason).
- The full risk reason (`risk_score`, `severity`, `flags`, hard rules) is logged server-side
  and shown ONLY on the authenticated Tier 2 screen — deliberately kept out of the agent
  response so the response is not an oracle a hostile agent can bisect.
- Audit events (`attempt`, `resolve`, `expire`) are written to the append-only audit log.

## 6. What is still a deliberate boundary (not an oversight)

- **Not in the payment path** — a real gate needs Checkout / `ChargePermissionId`.
- **Category truth is heuristic** — the authoritative source is the payment rail's MCC.
- **Nothing is charged** — fulfillment is `simulated`.
- **Shared SQLite file** — Tier 2 access is narrowed + token-gated; a full 3-service split
  (dedicated approvals store) is the production end-state.
