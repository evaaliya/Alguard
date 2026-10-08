-- actions_log.db schema.
-- NOTE: agent_id now holds the verified OAuth `sub` of the END USER (not the Alexa
-- client_id), so trust, merchant history and limits are per-user. If you have an
-- older actions_log.db, delete it -- the schema changed (expires_at added).

CREATE TABLE IF NOT EXISTS agent_trust (
    agent_id        TEXT PRIMARY KEY,
    trust_score     REAL NOT NULL DEFAULT 0.5,
    approved_count  INTEGER NOT NULL DEFAULT 0,
    denied_count    INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    action_id       TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    agent_id        TEXT NOT NULL,   -- verified end-user subject (owner of the action)
    item            TEXT,
    category        TEXT,            -- AS DECLARED BY THE AGENT (untrusted)
    merchant        TEXT,
    amount          REAL,
    created_at      TEXT NOT NULL,
    risk_score      REAL,
    category_severity TEXT,          -- SERVER-ASSESSED effective severity
    anomaly_flags   TEXT,
    status          TEXT NOT NULL,   -- OK | MONITOR | HALTED
    reason          TEXT,            -- internal; never returned to the agent
    expires_at      TEXT,            -- HALTED only: approval must happen before this
    resolution      TEXT,            -- APPROVED | DENIED | NULL
    resolved_at     TEXT,
    resolved_by     TEXT
);

CREATE INDEX IF NOT EXISTS idx_actions_owner   ON actions(agent_id, created_at);
CREATE INDEX IF NOT EXISTS idx_actions_session ON actions(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_actions_merch   ON actions(agent_id, merchant);
