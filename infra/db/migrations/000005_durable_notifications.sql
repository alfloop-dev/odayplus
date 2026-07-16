-- Durable Notifications Schema (ODP-PGAP-OBS-001)
CREATE TABLE IF NOT EXISTS notification_preferences (
    user_id TEXT PRIMARY KEY,
    channels TEXT NOT NULL, -- JSON list of channels, e.g. ["email", "sms"]
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS notification_deduplication (
    dedup_key TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_receipts (
    receipt_id TEXT PRIMARY KEY,
    notification_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    retry_count INTEGER NOT NULL DEFAULT 0,
    last_attempt TEXT,
    error_message TEXT,
    delivered_at TEXT
);
