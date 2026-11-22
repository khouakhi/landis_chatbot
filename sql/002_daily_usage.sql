-- LandIS assistant — daily question counter per visitor (no accounts).
-- visitor_key is a SHA-256 hash of the landis_visitor session cookie. No IP address is stored.
-- Apply with: uv run landis-db-init (safe to re-run).

CREATE TABLE IF NOT EXISTS landis_daily_usage (
    usage_date DATE NOT NULL,
    visitor_key TEXT NOT NULL,
    question_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (usage_date, visitor_key)
);
