CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    status      TEXT NOT NULL DEFAULT 'queued',
    prompt      TEXT NOT NULL,
    gate        JSONB,              -- what the human is being asked, while awaiting_approval
    resume      JSONB,              -- the human's decision, consumed by the worker
    result      JSONB,
    error       TEXT,
    attempts    INT NOT NULL DEFAULT 0,
    heartbeat   TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runs_status_created ON runs (status, created_at);
