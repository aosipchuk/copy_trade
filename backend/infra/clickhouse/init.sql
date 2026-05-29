CREATE DATABASE IF NOT EXISTS copytrade;

CREATE TABLE IF NOT EXISTS copytrade.trader_positions
(
    trader_address  String,
    coin            String,
    side            LowCardinality(String),
    szi             Float64,
    entry_px        Float64,
    unrealized_pnl  Float64,
    leverage        Float32,
    snapshot_at     DateTime
)
ENGINE = MergeTree()
PARTITION BY toYYYYMMDD(snapshot_at)
ORDER BY (trader_address, coin, snapshot_at)
TTL snapshot_at + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

CREATE TABLE IF NOT EXISTS copytrade.trader_pnl
(
    trader_address  String,
    ts              DateTime,
    pnl             Float64,
    roi             Float64,
    period          LowCardinality(String)
)
ENGINE = MergeTree()
PARTITION BY toYYYYMM(ts)
ORDER BY (trader_address, period, ts)
TTL ts + INTERVAL 365 DAY
SETTINGS index_granularity = 8192;
