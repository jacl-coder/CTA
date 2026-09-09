CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    trading_day TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    config_json TEXT NOT NULL
);
CREATE TABLE instruments (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    instrument_id TEXT NOT NULL,
    product TEXT NOT NULL,
    exchange TEXT NOT NULL CHECK(exchange IN ('SHFE', 'DCE', 'CFFEX')),
    multiplier INTEGER NOT NULL CHECK(multiplier > 0),
    tick_size TEXT NOT NULL,
    margin_rate TEXT NOT NULL,
    fee_per_lot TEXT NOT NULL,
    PRIMARY KEY(run_id, instrument_id)
);
CREATE TABLE accounts (
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    account_id TEXT NOT NULL,
    initial_capital TEXT NOT NULL,
    PRIMARY KEY(run_id, account_id)
);
CREATE TABLE account_books (
    run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    trading_day TEXT NOT NULL,
    opening_balance TEXT NOT NULL,
    realized_pnl TEXT NOT NULL,
    fees TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision >= 0),
    PRIMARY KEY(run_id, account_id),
    FOREIGN KEY(run_id, account_id) REFERENCES accounts(run_id, account_id)
);
CREATE TABLE trades (
    ingest_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    trading_day TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    side TEXT NOT NULL CHECK(side IN ('LONG', 'SHORT')),
    offset TEXT NOT NULL CHECK(offset IN ('OPEN', 'CLOSE_TODAY', 'CLOSE_YESTERDAY')),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    price TEXT NOT NULL,
    fee TEXT NOT NULL,
    source TEXT NOT NULL,
    UNIQUE(run_id, account_id, fill_id),
    UNIQUE(run_id, account_id, trading_day, sequence),
    FOREIGN KEY(run_id, account_id) REFERENCES accounts(run_id, account_id),
    FOREIGN KEY(run_id, instrument_id) REFERENCES instruments(run_id, instrument_id)
);
CREATE TABLE position_lots (
    run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK(side IN ('LONG', 'SHORT')),
    opened_day TEXT NOT NULL,
    opened_sequence INTEGER NOT NULL CHECK(opened_sequence > 0),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    open_price TEXT NOT NULL,
    basis TEXT NOT NULL,
    PRIMARY KEY(run_id, account_id, lot_id),
    FOREIGN KEY(run_id, account_id) REFERENCES accounts(run_id, account_id),
    FOREIGN KEY(run_id, instrument_id) REFERENCES instruments(run_id, instrument_id),
    FOREIGN KEY(run_id, account_id, lot_id) REFERENCES trades(run_id, account_id, fill_id)
);
CREATE TABLE close_allocations (
    allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    fill_id TEXT NOT NULL,
    lot_id TEXT NOT NULL,
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    basis TEXT NOT NULL,
    close_price TEXT NOT NULL,
    pnl TEXT NOT NULL,
    UNIQUE(run_id, account_id, fill_id, lot_id),
    FOREIGN KEY(run_id, account_id, fill_id) REFERENCES trades(run_id, account_id, fill_id),
    FOREIGN KEY(run_id, account_id, lot_id) REFERENCES trades(run_id, account_id, fill_id)
);
CREATE INDEX positions_by_instrument ON position_lots(run_id, instrument_id);
