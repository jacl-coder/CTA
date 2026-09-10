-- 追加式交易日志同时保存风险转换、拒绝和成交结果；启动时逐条重放核对。
CREATE TABLE trading_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
    policy_json TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0 CHECK(revision >= 0)
);
CREATE TABLE trading_journal (
    run_id TEXT NOT NULL REFERENCES trading_runs(run_id),
    revision INTEGER NOT NULL CHECK(revision > 0),
    command_json TEXT NOT NULL,
    outcome_json TEXT NOT NULL,
    PRIMARY KEY(run_id, revision)
);
