CREATE TABLE market_runs (
    run_id TEXT PRIMARY KEY REFERENCES trading_runs(run_id),
    plan_json TEXT NOT NULL,
    epoch_ms INTEGER NOT NULL,
    applied_sequence INTEGER NOT NULL DEFAULT 0 CHECK(applied_sequence >= 0)
);
CREATE TABLE market_frames (
    run_id TEXT NOT NULL REFERENCES market_runs(run_id),
    source_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    payload_json TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY(run_id, source_id, sequence)
);
CREATE TABLE market_event_observations (
    run_id TEXT NOT NULL REFERENCES market_runs(run_id),
    event_id INTEGER NOT NULL,
    occurred_ms INTEGER NOT NULL,
    detected_ms INTEGER NOT NULL,
    recovered INTEGER NOT NULL CHECK(recovered IN (0, 1)),
    PRIMARY KEY(run_id, event_id)
);
