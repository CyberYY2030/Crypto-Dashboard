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
    """
    CREATE TABLE IF NOT EXISTS alpha_universe (
        token_key TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        name TEXT,
        chain TEXT NOT NULL,
        contract_address TEXT NOT NULL,
        alpha_symbol TEXT,
        futures_symbol TEXT,
        primary_pool_id TEXT,
        market_cap_confidence TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        extra_json TEXT
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS alpha_screen_snapshots (
        ts TEXT NOT NULL,
        token_key TEXT NOT NULL,
        signal_label TEXT,
        score REAL,
        price_usd REAL,
        volume_24h REAL,
        volume_expansion_ratio REAL,
        liquidity_usd REAL,
        market_cap_usd REAL,
        market_cap_confidence TEXT,
        drawdown_from_alpha_open_pct REAL,
        drawdown_from_listing_reference_pct REAL,
        drawdown_from_ath_pct REAL,
        funding_rate REAL,
        open_interest_usd REAL,
        passed_layer1 INTEGER NOT NULL DEFAULT 0,
        extra_json TEXT,
        PRIMARY KEY (ts, token_key)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS alpha_holder_snapshots (
        ts TEXT NOT NULL,
        token_key TEXT NOT NULL,
        address TEXT NOT NULL,
        balance REAL,
        pct_supply REAL,
        address_label TEXT,
        entity_name TEXT,
        holder_type TEXT,
        is_excluded INTEGER NOT NULL DEFAULT 0,
        extra_json TEXT,
        PRIMARY KEY (ts, token_key, address)
    );
    """,
    """
    CREATE TABLE IF NOT EXISTS collection_runs (
        run_id TEXT PRIMARY KEY,
        job_name TEXT NOT NULL,
        effective_date TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        status TEXT NOT NULL,
        expected_core_metrics TEXT NOT NULL,
        actual_core_metrics TEXT,
        warnings_json TEXT NOT NULL DEFAULT '[]',
        error_summary TEXT,
        entrypoint TEXT NOT NULL
    );
    """,
    """CREATE TABLE IF NOT EXISTS alpha_screen_runs (
        run_id TEXT PRIMARY KEY, snapshot_ts TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
        status TEXT NOT NULL, universe_count INTEGER NOT NULL DEFAULT 0, eligible_count INTEGER NOT NULL DEFAULT 0,
        snapshot_count INTEGER NOT NULL DEFAULT 0, ready_count INTEGER NOT NULL DEFAULT 0, passed_count INTEGER NOT NULL DEFAULT 0,
        warnings_json TEXT NOT NULL DEFAULT '[]', error_summary TEXT, entrypoint TEXT NOT NULL, universe_synced INTEGER NOT NULL DEFAULT 0, universe_synced_at TEXT,
        current_pool_count INTEGER NOT NULL DEFAULT 0, target_count INTEGER NOT NULL DEFAULT 0,
        reference_ready_count INTEGER NOT NULL DEFAULT 0, reference_refreshed_count INTEGER NOT NULL DEFAULT 0
    );""",
    """CREATE TABLE IF NOT EXISTS alpha_holder_refresh_state (
        token_key TEXT PRIMARY KEY, attempted_at TEXT NOT NULL, last_run_id TEXT NOT NULL, outcome TEXT NOT NULL
    );""",
    """CREATE TABLE IF NOT EXISTS alpha_reference_cache (
        token_key TEXT PRIMARY KEY, payload_json TEXT, refreshed_at TEXT, attempted_at TEXT NOT NULL,
        outcome TEXT NOT NULL, error_summary TEXT
    );""",
]

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    for stmt in SCHEMA:
        conn.execute(stmt)
    for table in ("alpha_screen_snapshots", "alpha_holder_snapshots"):
        try: conn.execute(f"ALTER TABLE {table} ADD COLUMN run_id TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower(): raise
    try:
        conn.execute("ALTER TABLE alpha_screen_snapshots ADD COLUMN drawdown_from_listing_reference_pct REAL")
    except sqlite3.OperationalError as exc:
        if "duplicate column" not in str(exc).lower():
            raise
    for column in (
        "universe_synced_at TEXT",
        "current_pool_count INTEGER NOT NULL DEFAULT 0",
        "target_count INTEGER NOT NULL DEFAULT 0",
        "reference_ready_count INTEGER NOT NULL DEFAULT 0",
        "reference_refreshed_count INTEGER NOT NULL DEFAULT 0",
    ):
        try:
            conn.execute(f"ALTER TABLE alpha_screen_runs ADD COLUMN {column}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_screen_run_token ON alpha_screen_snapshots(run_id, token_key) WHERE run_id IS NOT NULL")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alpha_holder_run_token_address ON alpha_holder_snapshots(run_id, token_key, address) WHERE run_id IS NOT NULL")
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
    return get_latest_metrics_date(conn)["date"]


def _decode_json_list(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return []
    return decoded if isinstance(decoded, list) else []


def _collection_run_from_row(row: Optional[Tuple[Any, ...]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    keys = [
        "run_id", "job_name", "effective_date", "started_at", "completed_at", "status",
        "expected_core_metrics", "actual_core_metrics", "warnings_json", "error_summary", "entrypoint",
    ]
    result = dict(zip(keys, row))
    result["expected_core_metrics"] = _decode_json_list(result["expected_core_metrics"])
    result["actual_core_metrics"] = _decode_json_list(result["actual_core_metrics"])
    result["warnings"] = _decode_json_list(result.pop("warnings_json"))
    return result


def start_collection_run(
    conn: sqlite3.Connection,
    run_id: str,
    job_name: str,
    effective_date: str,
    expected_core_metrics: Iterable[str],
    entrypoint: str,
) -> None:
    conn.execute(
        """INSERT INTO collection_runs(
               run_id, job_name, effective_date, started_at, completed_at, status,
               expected_core_metrics, actual_core_metrics, warnings_json, error_summary, entrypoint
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            run_id,
            job_name,
            effective_date,
            dt.datetime.now(dt.timezone.utc).isoformat(),
            None,
            "running",
            json.dumps(list(expected_core_metrics)),
            json.dumps([]),
            json.dumps([]),
            None,
            entrypoint,
        ),
    )


def complete_collection_run(
    conn: sqlite3.Connection, run_id: str, actual_core_metrics: Iterable[str], warnings: Iterable[str]
) -> None:
    conn.execute(
        """UPDATE collection_runs
           SET completed_at=?, status='complete', actual_core_metrics=?, warnings_json=?, error_summary=NULL
           WHERE run_id=?""",
        (
            dt.datetime.now(dt.timezone.utc).isoformat(),
            json.dumps(list(actual_core_metrics)),
            json.dumps(list(warnings), ensure_ascii=False),
            run_id,
        ),
    )


def fail_collection_run(
    conn: sqlite3.Connection,
    run_id: str,
    actual_core_metrics: Iterable[str],
    warnings: Iterable[str],
    error_summary: str,
) -> None:
    conn.execute(
        """UPDATE collection_runs
           SET completed_at=?, status='failed', actual_core_metrics=?, warnings_json=?, error_summary=?
           WHERE run_id=?""",
        (
            dt.datetime.now(dt.timezone.utc).isoformat(),
            json.dumps(list(actual_core_metrics)),
            json.dumps(list(warnings), ensure_ascii=False),
            error_summary[:500],
            run_id,
        ),
    )


def get_latest_complete_collection_run(
    conn: sqlite3.Connection, job_name: str = "daily_macro"
) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT run_id, job_name, effective_date, started_at, completed_at, status,
                  expected_core_metrics, actual_core_metrics, warnings_json, error_summary, entrypoint
           FROM collection_runs
           WHERE job_name=? AND status='complete'
           ORDER BY effective_date DESC, completed_at DESC, run_id DESC
           LIMIT 1""",
        (job_name,),
    ).fetchone()
    return _collection_run_from_row(row)


def get_latest_collection_run(conn: sqlite3.Connection, job_name: str = "daily_macro") -> Optional[Dict[str, Any]]:
    row = conn.execute(
        """SELECT run_id, job_name, effective_date, started_at, completed_at, status,
                  expected_core_metrics, actual_core_metrics, warnings_json, error_summary, entrypoint
           FROM collection_runs
           WHERE job_name=?
           ORDER BY started_at DESC, run_id DESC
           LIMIT 1""",
        (job_name,),
    ).fetchone()
    return _collection_run_from_row(row)


def get_latest_metrics_date(conn: sqlite3.Connection, job_name: str = "daily_macro") -> Dict[str, Any]:
    complete_run = get_latest_complete_collection_run(conn, job_name)
    if complete_run:
        return {"date": complete_run["effective_date"], "legacy_unverified": False, "run": complete_run}
    row = conn.execute("SELECT MAX(date) FROM metrics").fetchone()
    legacy_date = row[0] if row and row[0] else None
    return {"date": legacy_date, "legacy_unverified": bool(legacy_date), "run": None}

def fetch_metrics_for_date(conn: sqlite3.Connection, date: str) -> Dict[str, float]:
    rows = conn.execute("SELECT metric, value FROM metrics WHERE date=?", (date,)).fetchall()
    return {m: v for (m, v) in rows}


def fetch_metric_detail_for_date(conn: sqlite3.Connection, date: str, metric: str) -> Optional[Dict[str, Any]]:
    row = conn.execute(
        "SELECT value, source, extra_json FROM metrics WHERE date=? AND metric=?",
        (date, metric),
    ).fetchone()
    if not row:
        return None
    extra_json = row[2]
    extra = json.loads(extra_json) if extra_json else None
    return {"value": row[0], "source": row[1], "extra": extra}

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


def upsert_alpha_universe(conn: sqlite3.Connection, row: Dict[str, Any]):
    extra_json = json.dumps(row.get("extra_json"), ensure_ascii=False) if row.get("extra_json") else None
    conn.execute(
        """INSERT INTO alpha_universe(
               token_key, symbol, name, chain, contract_address, alpha_symbol,
               futures_symbol, primary_pool_id, market_cap_confidence, is_active, extra_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(token_key) DO UPDATE SET
               symbol=excluded.symbol,
               name=excluded.name,
               chain=excluded.chain,
               contract_address=excluded.contract_address,
               alpha_symbol=excluded.alpha_symbol,
               futures_symbol=excluded.futures_symbol,
               primary_pool_id=excluded.primary_pool_id,
               market_cap_confidence=excluded.market_cap_confidence,
               is_active=excluded.is_active,
               extra_json=excluded.extra_json
        """,
        (
            row["token_key"],
            row["symbol"],
            row.get("name"),
            row["chain"],
            row["contract_address"],
            row.get("alpha_symbol"),
            row.get("futures_symbol"),
            row.get("primary_pool_id"),
            row.get("market_cap_confidence"),
            int(row.get("is_active", 1)),
            extra_json,
        ),
    )


def fetch_alpha_universe(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.execute(
        """SELECT token_key, symbol, name, chain, contract_address, alpha_symbol,
                  futures_symbol, primary_pool_id, market_cap_confidence, is_active, extra_json
           FROM alpha_universe
           WHERE is_active=1
           ORDER BY symbol
        """
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def insert_alpha_screen_snapshot(conn: sqlite3.Connection, row: Dict[str, Any]):
    extra_json = json.dumps(row.get("extra_json"), ensure_ascii=False) if row.get("extra_json") else None
    conn.execute(
        """INSERT INTO alpha_screen_snapshots(
               ts, token_key, signal_label, score, price_usd, volume_24h, volume_expansion_ratio,
               liquidity_usd, market_cap_usd, market_cap_confidence, drawdown_from_alpha_open_pct,
               drawdown_from_listing_reference_pct,
               drawdown_from_ath_pct, funding_rate, open_interest_usd, passed_layer1, extra_json, run_id
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row["ts"],
            row["token_key"],
            row.get("signal_label"),
            row.get("score"),
            row.get("price_usd"),
            row.get("volume_24h"),
            row.get("volume_expansion_ratio"),
            row.get("liquidity_usd"),
            row.get("market_cap_usd"),
            row.get("market_cap_confidence"),
            row.get("drawdown_from_alpha_open_pct"),
            row.get("drawdown_from_listing_reference_pct"),
            row.get("drawdown_from_ath_pct"),
            row.get("funding_rate"),
            row.get("open_interest_usd"),
            int(row.get("passed_layer1", 0)),
            extra_json,
            row.get("run_id"),
        ),
    )


def insert_alpha_holder_snapshot(conn: sqlite3.Connection, ts: str, token_key: str, row: Dict[str, Any]):
    extra_json = json.dumps(row.get("extra_json"), ensure_ascii=False) if row.get("extra_json") else None
    conn.execute(
        """INSERT OR REPLACE INTO alpha_holder_snapshots(
               ts, token_key, address, balance, pct_supply, address_label, entity_name, holder_type, is_excluded, extra_json, run_id
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ts,
            token_key,
            row["address"],
            row["balance"] if "balance" in row else None,
            row.get("pct_supply"),
            row.get("address_label"),
            row.get("entity_name"),
            row.get("holder_type"),
            int(row.get("is_excluded", 0)),
            extra_json,
            row.get("run_id"),
        ),
    )


def start_alpha_screen_run(conn, run_id, snapshot_ts, entrypoint, universe_count, universe_synced):
    conn.execute(
        "INSERT INTO alpha_screen_runs("
        "run_id,snapshot_ts,started_at,status,entrypoint,universe_count,universe_synced"
        ") VALUES(?,?,?,?,?,?,?)",
        (
            run_id,
            snapshot_ts,
            dt.datetime.now(dt.timezone.utc).isoformat(),
            "running",
            entrypoint,
            universe_count,
            int(universe_synced),
        ),
    )


def finish_alpha_screen_run(conn, run_id, status, counts, warnings, error_summary=None):
    conn.execute(
        "UPDATE alpha_screen_runs SET completed_at=?,status=?,eligible_count=?,snapshot_count=?,"
        "ready_count=?,passed_count=?,current_pool_count=?,target_count=?,reference_ready_count=?,"
        "reference_refreshed_count=?,warnings_json=?,error_summary=? WHERE run_id=?",
        (
            dt.datetime.now(dt.timezone.utc).isoformat(),
            status,
            counts.get("eligible", 0),
            counts.get("snapshot", 0),
            counts.get("ready", 0),
            counts.get("passed", 0),
            counts.get("current_pool", 0),
            counts.get("target", 0),
            counts.get("reference_ready", 0),
            counts.get("reference_refreshed", 0),
            json.dumps(warnings),
            error_summary,
            run_id,
        ),
    )


def latest_alpha_run(conn, complete_only=False):
    where_clause = "WHERE status='complete' " if complete_only else ""
    row = conn.execute(
        "SELECT run_id,snapshot_ts,started_at,completed_at,status,universe_count,eligible_count,"
        "snapshot_count,ready_count,passed_count,current_pool_count,target_count,reference_ready_count,"
        "reference_refreshed_count,warnings_json,error_summary,entrypoint,universe_synced,"
        "universe_synced_at FROM alpha_screen_runs "
        + where_clause
        + "ORDER BY started_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    keys = (
        "run_id snapshot_ts started_at completed_at status universe_count eligible_count "
        "snapshot_count ready_count passed_count current_pool_count target_count reference_ready_count "
        "reference_refreshed_count warnings_json error_summary entrypoint universe_synced "
        "universe_synced_at"
    ).split()
    result = dict(zip(keys, row))
    result["warnings"] = json.loads(result.pop("warnings_json") or "[]")
    return result


def fetch_alpha_reference_cache(conn, token_keys):
    if not token_keys:
        return {}
    placeholders = ",".join("?" for _ in token_keys)
    rows = conn.execute(
        "SELECT token_key,payload_json,refreshed_at,attempted_at,outcome,error_summary "
        f"FROM alpha_reference_cache WHERE token_key IN ({placeholders})",
        token_keys,
    ).fetchall()
    return {
        row[0]: {
            "payload": json.loads(row[1]) if row[1] else None,
            "refreshed_at": row[2],
            "attempted_at": row[3],
            "outcome": row[4],
            "error_summary": row[5],
        }
        for row in rows
    }


def upsert_alpha_reference_cache(conn, token_key, payload, refreshed_at, attempted_at, outcome, error_summary=None):
    if outcome == "success":
        conn.execute(
            "INSERT INTO alpha_reference_cache(token_key,payload_json,refreshed_at,attempted_at,outcome,error_summary) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(token_key) DO UPDATE SET "
            "payload_json=excluded.payload_json,refreshed_at=excluded.refreshed_at,"
            "attempted_at=excluded.attempted_at,outcome=excluded.outcome,error_summary=excluded.error_summary",
            (token_key, json.dumps(payload), refreshed_at, attempted_at, outcome, None),
        )
        return
    conn.execute(
        "INSERT INTO alpha_reference_cache(token_key,payload_json,refreshed_at,attempted_at,outcome,error_summary) "
        "VALUES(?,?,?,?,?,?) ON CONFLICT(token_key) DO UPDATE SET "
        "attempted_at=excluded.attempted_at,outcome=excluded.outcome,error_summary=excluded.error_summary",
        (token_key, None, None, attempted_at, outcome, error_summary),
    )


def claim_alpha_screen_run(conn, snapshot_ts, entrypoint, universe_count, refresh_minutes, stale_run_minutes, force=False):
    now = dt.datetime.now(dt.timezone.utc)
    conn.execute("BEGIN IMMEDIATE")
    running = conn.execute(
        "SELECT run_id,started_at FROM alpha_screen_runs WHERE status='running'"
    ).fetchall()
    fresh = [
        row
        for row in running
        if (now - dt.datetime.fromisoformat(row[1])).total_seconds() <= stale_run_minutes * 60
    ]
    if fresh:
        conn.rollback()
        return None, "running"
    for stale in running:
        finish_alpha_screen_run(conn, stale[0], "failed", {}, [], "stale_running")
    complete = latest_alpha_run(conn, True)
    if complete and not force and (
        now - dt.datetime.fromisoformat(complete["completed_at"])
    ).total_seconds() < refresh_minutes * 60:
        conn.commit()
        return None, "cadence"
    import uuid
    run_id = str(uuid.uuid4())
    start_alpha_screen_run(conn, run_id, snapshot_ts, entrypoint, universe_count, False)
    conn.commit()
    return run_id, None


def fetch_latest_alpha_candidates(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    run=latest_alpha_run(conn, True)
    if not run:
        return []
    cur = conn.execute(
        """SELECT s.ts, s.token_key, u.symbol, u.chain, u.futures_symbol,
                  s.signal_label, s.score, s.price_usd, s.volume_24h, s.volume_expansion_ratio,
                  s.liquidity_usd, s.market_cap_usd, s.market_cap_confidence,
                  s.drawdown_from_alpha_open_pct, s.drawdown_from_listing_reference_pct, s.drawdown_from_ath_pct,
                  s.funding_rate, s.open_interest_usd, s.extra_json
           FROM alpha_screen_snapshots s
           JOIN alpha_universe u ON u.token_key = s.token_key
            WHERE s.run_id = ? AND s.passed_layer1 = 1
           ORDER BY s.score DESC, s.volume_expansion_ratio DESC
        """,
        (run["run_id"],),
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_latest_alpha_snapshot(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    run=latest_alpha_run(conn, True)
    if not run:
        return []
    cur = conn.execute(
        """SELECT s.ts, s.token_key, u.symbol, u.chain, u.futures_symbol,
                  s.signal_label, s.score, s.price_usd, s.volume_24h, s.volume_expansion_ratio,
                  s.liquidity_usd, s.market_cap_usd, s.market_cap_confidence,
                  s.drawdown_from_alpha_open_pct, s.drawdown_from_listing_reference_pct, s.drawdown_from_ath_pct,
                  s.funding_rate, s.open_interest_usd, s.passed_layer1, s.extra_json
           FROM alpha_screen_snapshots s
           JOIN alpha_universe u ON u.token_key = s.token_key
            WHERE s.run_id = ?
           ORDER BY s.passed_layer1 DESC, s.score DESC, s.volume_expansion_ratio DESC
        """,
        (run["run_id"],),
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def fetch_recent_alpha_volumes(conn: sqlite3.Connection, token_key: str, limit: int = 7) -> List[float]:
    rows = conn.execute(
        """SELECT volume_24h
           FROM alpha_screen_snapshots
           WHERE token_key=? AND volume_24h IS NOT NULL
           ORDER BY ts DESC
           LIMIT ?
        """,
        (token_key, limit),
    ).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]
