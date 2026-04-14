from __future__ import annotations

import sqlite3
import json
import datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS metrics (
        date TEXT NOT NULL,
        metric TEXT NOT NULL,
        value REAL,
        source TEXT,
        extra_json TEXT,
        PRIMARY KEY (date, metric)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS prices (
        date TEXT NOT NULL,
        symbol TEXT NOT NULL,
        close REAL,
        high REAL,
        low REAL,
        volume REAL,
        source TEXT,
        PRIMARY KEY (date, symbol)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS alerts (
        ts TEXT NOT NULL,
        alert_type TEXT NOT NULL,
        payload_json TEXT
    );
    """,
]

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    for stmt in SCHEMA:
        conn.execute(stmt)
    conn.commit()
    return conn

def upsert_metric(conn: sqlite3.Connection, date: str, metric: str, value: Optional[float], source: str, extra: Optional[Dict[str, Any]]=None):
    extra_json = json.dumps(extra, ensure_ascii=False) if extra else None
    conn.execute(
        """INSERT INTO metrics(date, metric, value, source, extra_json)
               VALUES(?,?,?,?,?)
               ON CONFLICT(date, metric) DO UPDATE SET
                 value=excluded.value,
                 source=excluded.source,
                 extra_json=excluded.extra_json
        """,
        (date, metric, value, source, extra_json),
    )

def upsert_price(conn: sqlite3.Connection, date: str, symbol: str, close: Optional[float], high: Optional[float], low: Optional[float], volume: Optional[float], source: str):
    conn.execute(
        """INSERT INTO prices(date, symbol, close, high, low, volume, source)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(date, symbol) DO UPDATE SET
                 close=excluded.close,
                 high=excluded.high,
                 low=excluded.low,
                 volume=excluded.volume,
                 source=excluded.source
        """,
        (date, symbol, close, high, low, volume, source),
    )

def insert_alert(conn: sqlite3.Connection, ts: str, alert_type: str, payload: Dict[str, Any]):
    import json
    conn.execute(
        "INSERT INTO alerts(ts, alert_type, payload_json) VALUES(?,?,?)",
        (ts, alert_type, json.dumps(payload, ensure_ascii=False)),
    )

def fetch_latest_date(conn: sqlite3.Connection) -> Optional[str]:
    row = conn.execute("SELECT MAX(date) FROM metrics").fetchone()
    return row[0] if row and row[0] else None

def fetch_metrics_for_date(conn: sqlite3.Connection, date: str) -> Dict[str, float]:
    rows = conn.execute("SELECT metric, value FROM metrics WHERE date=?", (date,)).fetchall()
    return {m: v for (m, v) in rows}

def fetch_metric_series(conn: sqlite3.Connection, metric: str, limit: int = 120) -> List[Tuple[str, float]]:
    rows = conn.execute(
        "SELECT date, value FROM metrics WHERE metric=? AND value IS NOT NULL ORDER BY date DESC LIMIT ?",
        (metric, limit),
    ).fetchall()
    rows = list(reversed(rows))
    return rows

def fetch_price_series(conn: sqlite3.Connection, symbol: str, limit: int = 180):
    rows = conn.execute(
        "SELECT date, close FROM prices WHERE symbol=? AND close IS NOT NULL ORDER BY date DESC LIMIT ?",
        (symbol, limit),
    ).fetchall()
    rows = list(reversed(rows))
    return rows

def fetch_metric_values(conn: sqlite3.Connection, metric: str, limit: int = 365):
    rows = conn.execute(
        "SELECT value FROM metrics WHERE metric=? AND value IS NOT NULL ORDER BY date DESC LIMIT ?",
        (metric, limit),
    ).fetchall()
    return [r[0] for r in rows]


def fetch_existing_dates_for_prices(conn: sqlite3.Connection, symbol: str) -> set[str]:
    rows = conn.execute("SELECT date FROM prices WHERE symbol=?", (symbol,)).fetchall()
    return {r[0] for r in rows}

def fetch_existing_dates_for_metric(conn: sqlite3.Connection, metric: str) -> set[str]:
    rows = conn.execute("SELECT date FROM metrics WHERE metric=?", (metric,)).fetchall()
    return {r[0] for r in rows}
