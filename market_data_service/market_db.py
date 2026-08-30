"""
market_db.py
============
Storage layer for the near-real-time equities + bond repricing pipeline.

Uses SQLite by default (consistent with pipeline.py / signals.db) so this
runs with zero extra infrastructure on your Mac. The schema below is the
same shape as the TimescaleDB spec in the architecture notes — if you
later move to Postgres+TimescaleDB, only MARKET_DB_URL changes and you
run `SELECT create_hypertable('equity_prices','ts')` etc. once. No
application code needs to change.

Tables:
    equity_prices     (ts, ticker, price, bid, ask, volume, source)
    rate_proxy_prices (ts, contract, price, yield_equiv)
    bond_prices       (ts, position_id, model_price, oas, anchor_ts, anchor_source)
    reconciliation_log(ts, job, status, detail)
"""

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

MARKET_DB_PATH = os.environ.get("MARKET_DB_PATH", "market_data.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(MARKET_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS equity_prices (
            ts      TEXT NOT NULL,
            ticker  TEXT NOT NULL,
            price   REAL,
            bid     REAL,
            ask     REAL,
            volume  INTEGER,
            source  TEXT DEFAULT 'webull',
            PRIMARY KEY (ts, ticker)
        );

        CREATE TABLE IF NOT EXISTS rate_proxy_prices (
            ts           TEXT NOT NULL,
            contract     TEXT NOT NULL,   -- ZN, ZB, TN, UB
            price        REAL,
            yield_equiv  REAL,
            PRIMARY KEY (ts, contract)
        );

        CREATE TABLE IF NOT EXISTS bond_prices (
            ts             TEXT NOT NULL,
            position_id    TEXT NOT NULL,
            model_price    REAL,
            oas            REAL,
            anchor_ts      TEXT,
            anchor_source  TEXT,          -- 'rate_proxy' | 'trace_eod' | 'pmms'
            PRIMARY KEY (ts, position_id)
        );

        CREATE TABLE IF NOT EXISTS reconciliation_log (
            ts      TEXT NOT NULL,
            job     TEXT NOT NULL,        -- 'trace_eod' | 'pmms_weekly'
            status  TEXT NOT NULL,        -- 'ok' | 'error'
            detail  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_equity_ticker_ts   ON equity_prices(ticker, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_bond_position_ts   ON bond_prices(position_id, ts DESC);
        """)
    print(f"  ✓ market_data.db initialised → {Path(MARKET_DB_PATH).resolve()}")


def insert_equity_prices(rows):
    """rows: list of dicts with ts, ticker, price, bid, ask, volume, source"""
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO equity_prices (ts, ticker, price, bid, ask, volume, source)
               VALUES (:ts, :ticker, :price, :bid, :ask, :volume, :source)""",
            rows,
        )


def insert_rate_proxy_prices(rows):
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO rate_proxy_prices (ts, contract, price, yield_equiv)
               VALUES (:ts, :contract, :price, :yield_equiv)""",
            rows,
        )


def insert_bond_prices(rows):
    with get_conn() as conn:
        conn.executemany(
            """INSERT OR REPLACE INTO bond_prices
               (ts, position_id, model_price, oas, anchor_ts, anchor_source)
               VALUES (:ts, :position_id, :model_price, :oas, :anchor_ts, :anchor_source)""",
            rows,
        )


def log_reconciliation(job, status, detail=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO reconciliation_log (ts, job, status, detail) VALUES (datetime('now'), ?, ?, ?)",
            (job, status, detail),
        )


def latest_equity_prices(tickers=None):
    with get_conn() as conn:
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            q = f"""
                SELECT e.* FROM equity_prices e
                INNER JOIN (
                    SELECT ticker, MAX(ts) AS max_ts FROM equity_prices
                    WHERE ticker IN ({placeholders}) GROUP BY ticker
                ) latest ON e.ticker = latest.ticker AND e.ts = latest.max_ts
            """
            return [dict(r) for r in conn.execute(q, tickers).fetchall()]
        q = """
            SELECT e.* FROM equity_prices e
            INNER JOIN (SELECT ticker, MAX(ts) AS max_ts FROM equity_prices GROUP BY ticker) latest
            ON e.ticker = latest.ticker AND e.ts = latest.max_ts
        """
        return [dict(r) for r in conn.execute(q).fetchall()]


def latest_rate_proxy():
    with get_conn() as conn:
        q = """
            SELECT r.* FROM rate_proxy_prices r
            INNER JOIN (SELECT contract, MAX(ts) AS max_ts FROM rate_proxy_prices GROUP BY contract) latest
            ON r.contract = latest.contract AND r.ts = latest.max_ts
        """
        return [dict(r) for r in conn.execute(q).fetchall()]


def latest_bond_prices():
    with get_conn() as conn:
        q = """
            SELECT b.* FROM bond_prices b
            INNER JOIN (SELECT position_id, MAX(ts) AS max_ts FROM bond_prices GROUP BY position_id) latest
            ON b.position_id = latest.position_id AND b.ts = latest.max_ts
        """
        return [dict(r) for r in conn.execute(q).fetchall()]


if __name__ == "__main__":
    init_db()
