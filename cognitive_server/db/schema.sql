-- Cognitive Local Database Schema
-- SQLite — 48-hour rolling window
-- Auto-created on first server start

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- Raw behavioral signals captured from browser extension + desktop agent
CREATE TABLE IF NOT EXISTS signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL,
    timestamp       TEXT NOT NULL,           -- ISO-8601 UTC
    kpm             REAL DEFAULT 0,          -- keystrokes per minute
    inter_key_avg   REAL DEFAULT 0,          -- average inter-keystroke interval (ms)
    switch_rate     REAL DEFAULT 0,          -- window/tab switches per minute
    scroll_velocity REAL DEFAULT 0,          -- average scroll speed (px/sec)
    scroll_delta    REAL DEFAULT 0,          -- net scroll direction magnitude
    mouse_entropy   REAL DEFAULT 0,          -- mouse movement randomness (0-1)
    idle_ratio      REAL DEFAULT 0,          -- fraction of window with no input (0-1)
    tab_count       INTEGER DEFAULT 1,       -- number of open tabs
    domain_switches INTEGER DEFAULT 0,       -- unique domains visited in window
    time_of_day     REAL DEFAULT 0,          -- sin/cos encoded hour (-1 to 1)
    active_url      TEXT DEFAULT '',         -- current page URL (domain only)
    active_title    TEXT DEFAULT '',         -- document title
    idle_seconds    INTEGER DEFAULT 0,       -- seconds since last input event
    created_at      TEXT DEFAULT (datetime('now'))
);

-- CLS inference results (populated from Phase 2 onward)
CREATE TABLE IF NOT EXISTS load_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,           -- ISO-8601 UTC
    cls_score       REAL NOT NULL,           -- 0.0 to 100.0
    confidence      REAL DEFAULT 1.0,        -- 0.0 to 1.0
    load_state      TEXT DEFAULT 'learning', -- restorative/light/focused/heavy/overloaded/learning
    source          TEXT DEFAULT 'model',    -- model / heuristic / fallback
    features_json   TEXT DEFAULT '{}'        -- raw 8-dim feature vector as JSON
);

-- Held notifications waiting for release
CREATE TABLE IF NOT EXISTS held_notifications (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,           -- browser / gmail / slack / calendar
    sender          TEXT DEFAULT '',         -- sender name or email
    preview         TEXT DEFAULT '',         -- message preview text
    held_at         TEXT NOT NULL,           -- ISO-8601 UTC
    urgency_score   REAL DEFAULT 0,          -- 0.0 to 1.0 (higher = more urgent)
    released_at     TEXT,                    -- NULL = still held
    release_reason  TEXT DEFAULT ''          -- timeout / smart_release / manual / urgency_bypass
);

-- Complete intervention log (read-only audit trail)
CREATE TABLE IF NOT EXISTS interventions_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,           -- ISO-8601 UTC
    type            TEXT NOT NULL,           -- notification_held / notification_released / draft_generated / calendar_responded / badge_update
    details         TEXT NOT NULL,           -- JSON payload with action specifics
    cls_at_time     REAL DEFAULT 0
);

-- Performance indexes for rolling window queries
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_signals_session ON signals(session_id);
CREATE INDEX IF NOT EXISTS idx_load_ts ON load_history(timestamp);
CREATE INDEX IF NOT EXISTS idx_held_ts ON held_notifications(held_at);
CREATE INDEX IF NOT EXISTS idx_interventions_ts ON interventions_log(timestamp);

-- Trigger: auto-purge signals older than 48 hours (runs on every insert)
-- Note: actual purge handled by background task; this is a safety net