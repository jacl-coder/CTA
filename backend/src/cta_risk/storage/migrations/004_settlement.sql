CREATE TABLE settlement_runs (
    run_id TEXT PRIMARY KEY REFERENCES trading_runs(run_id),
    config_json TEXT NOT NULL
);
CREATE TABLE daily_reports (
    run_id TEXT NOT NULL REFERENCES settlement_runs(run_id),
    trading_day TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    PRIMARY KEY(run_id, trading_day)
);
