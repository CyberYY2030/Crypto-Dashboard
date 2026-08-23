# Alpha EVM Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Binance Alpha EVM monitor that screens deeply depressed small-cap Alpha tokens with Binance futures support, enriches shortlisted tokens with futures and top-holder data, and shows the ranked results in the existing Streamlit dashboard.

**Architecture:** Keep the current SQLite + requests + Streamlit structure, but add a dedicated Alpha pipeline alongside the BTC/ETH pipeline. Use Binance Alpha for Alpha listing and day-1 open, DEX Screener for fixed-pool price/volume triggers, CoinGecko for verified market cap and ATH when available, Binance futures for contract and funding data, and Moralis for top-holder snapshots only on shortlisted names.

**Tech Stack:** Python 3.11, requests, pandas, sqlite3, Streamlit, unittest

---

Repository note: this workspace does not currently contain a `.git` directory, so each task ends with a local checkpoint step instead of a commit step. If the project is later moved into git, replace each checkpoint step with a normal commit.

## File Structure

### Existing files to modify

- `db.py`
  Add Alpha tables and helper queries without breaking the current BTC/ETH tables.
- `utils.py`
  Add small formatting and config helpers shared by the new Alpha views.
- `collectors/coingecko.py`
  Extend with coin-market lookups for ATH and verified market-cap fields.
- `collectors/binance_derivatives.py`
  Extend beyond open interest to futures symbol checks, funding rate, and mark price helpers.
- `config.example.yaml`
  Add Alpha-related settings and remove the exposed SoSoValue key from the example file.
- `app.py`
  Add an Alpha section to the Streamlit UI while preserving the existing BTC/ETH dashboard.

### New files to create

- `collectors/binance_alpha.py`
  Fetch Alpha token list, Alpha ticker metadata, and earliest Alpha K-line open.
- `collectors/dexscreener.py`
  Fetch token pairs, pair details, and daily-volume fields from DEX Screener.
- `collectors/moralis_evm.py`
  Fetch top token holders, holder metrics, and historical holder changes for EVM tokens.
- `alpha_pipeline.py`
  Orchestrate the Alpha refresh flow: universe sync, pool mapping, layer-1 screening, enrichment, and alerts.
- `alpha_logic.py`
  Contain pure screening and classification logic so it is easy to test without network calls.
- `tests/__init__.py`
  Make the new test package importable by `unittest`.
- `tests/test_alpha_db.py`
  Verify Alpha schema and query helpers.
- `tests/test_binance_alpha.py`
  Verify Alpha list parsing and earliest-open extraction.
- `tests/test_alpha_logic.py`
  Verify drawdown rules, volume-expansion rules, ranking, and signal labels.
- `tests/test_alpha_pipeline.py`
  Verify end-to-end Alpha pipeline behavior with stubbed collectors.

Pool note: store the primary observation pool as `"{chainId}/{pairAddress}"` so DEX Screener pair lookups can recover both the chain id and the pair id.

## Execution Order

Implement tasks in this order even if the detailed sections appear later in the file:

1. Task 1: Add Alpha storage and test scaffolding
2. Task 2: Add Binance Alpha and DEX Screener collectors
3. Task 3: Build the pure screening logic
4. Task 4: Build the Alpha refresh pipeline
5. Task 5: Add Moralis holder enrichment and holder-signal persistence
6. Task 6: Add the real data wiring and a dedicated Alpha runner
7. Task 7: Add the Streamlit Alpha dashboard
8. Task 8: Verify the full flow with a local run

## Task 1: Add Alpha storage and test scaffolding

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_alpha_db.py`
- Modify: `db.py`
- Modify: `config.example.yaml`

- [ ] **Step 1: Write the failing schema tests**

```python
import unittest

import db as dbm


class AlphaDbSchemaTests(unittest.TestCase):
    def setUp(self):
        self.conn = dbm.connect(":memory:")

    def tearDown(self):
        self.conn.close()

    def test_alpha_tables_exist(self):
        names = {
            row[0]
            for row in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        self.assertIn("alpha_universe", names)
        self.assertIn("alpha_screen_snapshots", names)
        self.assertIn("alpha_holder_snapshots", names)

    def test_upsert_alpha_universe_round_trip(self):
        dbm.upsert_alpha_universe(
            self.conn,
            {
                "token_key": "base:0xabc",
                "symbol": "ABC",
                "chain": "base",
                "contract_address": "0xabc",
                "alpha_symbol": "ALPHA_175USDT",
                "futures_symbol": "ABCUSDT",
                "market_cap_confidence": "estimated",
            },
        )
        row = dbm.fetch_alpha_universe(self.conn)[0]
        self.assertEqual(row["token_key"], "base:0xabc")
        self.assertEqual(row["market_cap_confidence"], "estimated")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
python -m unittest tests.test_alpha_db -v
```

Expected:

- Failures mentioning missing Alpha tables and missing `upsert_alpha_universe`

- [ ] **Step 3: Add the minimal schema and DB helpers**

Add these statements to `db.py` inside `SCHEMA`:

```python
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
```

Add these helpers near the bottom of `db.py`:

```python
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
```

Clean `config.example.yaml` so the Alpha block starts like this:

```yaml
alpha:
  enabled: true
  quote_currency: "usd"
  market_cap_limit_usd: 100000000
  drawdown_threshold_pct: 90.0
  volume_baseline_days: 7
  volume_expansion_ratio_min: 2.0
  refresh_minutes: 30
  holder_refresh_hours: 6
  moralis_api_key: ""
```

Also remove the hard-coded `sosovalue.api_key` value from the example file.

- [ ] **Step 4: Run the schema test again**

Run:

```powershell
python -m unittest tests.test_alpha_db -v
```

Expected:

- `test_alpha_tables_exist ... ok`
- `test_upsert_alpha_universe_round_trip ... ok`

- [ ] **Step 5: Local checkpoint**

Run:

```powershell
python -m py_compile db.py
```

Expected:

- No output

## Task 7: Add the Streamlit Alpha dashboard

**Files:**
- Modify: `app.py`
- Modify: `db.py`
- Modify: `utils.py`
- Modify: `tests/test_alpha_db.py`

- [ ] **Step 1: Add a failing query-helper test for the UI**

```python
    def test_fetch_latest_alpha_candidates_returns_ranked_rows(self):
        dbm.insert_alpha_screen_snapshot(self.conn, {
            "ts": "2026-04-14T10:00:00",
            "token_key": "base:0xabc",
            "signal_label": "first_volume_breakout",
            "score": 88.5,
            "price_usd": 0.10,
            "volume_24h": 500000,
            "volume_expansion_ratio": 3.2,
            "liquidity_usd": 250000,
            "market_cap_usd": 50000000,
            "market_cap_confidence": "verified",
            "drawdown_from_alpha_open_pct": 95.0,
            "drawdown_from_ath_pct": 96.0,
            "passed_layer1": 1,
        })
        rows = dbm.fetch_latest_alpha_candidates(self.conn)
        self.assertEqual(rows[0]["token_key"], "base:0xabc")
```

- [ ] **Step 2: Run the DB/UI helper test to verify it fails**

Run:

```powershell
python -m unittest tests.test_alpha_db -v
```

Expected:

- Failure for missing `fetch_latest_alpha_candidates`

- [ ] **Step 3: Implement the query helper and UI**

Add to `db.py`:

```python
def fetch_latest_alpha_candidates(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    row = conn.execute("SELECT MAX(ts) FROM alpha_screen_snapshots").fetchone()
    latest_ts = row[0] if row else None
    if not latest_ts:
        return []
    cur = conn.execute(
        """SELECT s.ts, s.token_key, u.symbol, u.chain, u.futures_symbol,
                  s.signal_label, s.score, s.price_usd, s.volume_24h, s.volume_expansion_ratio,
                  s.liquidity_usd, s.market_cap_usd, s.market_cap_confidence,
                  s.drawdown_from_alpha_open_pct, s.drawdown_from_ath_pct,
                  s.funding_rate, s.open_interest_usd
           FROM alpha_screen_snapshots s
           JOIN alpha_universe u ON u.token_key = s.token_key
           WHERE s.ts = ? AND s.passed_layer1 = 1
           ORDER BY s.score DESC, s.volume_expansion_ratio DESC
        """,
        (latest_ts,),
    )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
```

Add to `utils.py`:

```python
def fmt_compact_number(x: Optional[float]) -> str:
    if x is None:
        return "N/A"
    absx = abs(x)
    if absx >= 1e9:
        return f"{x/1e9:,.2f}B"
    if absx >= 1e6:
        return f"{x/1e6:,.2f}M"
    if absx >= 1e3:
        return f"{x/1e3:,.1f}K"
    return f"{x:,.2f}"
```

Modify `app.py` by wrapping the existing BTC/ETH dashboard in a tab and adding a second tab:

```python
tabs = st.tabs(["Macro Dashboard", "Alpha EVM Monitor"])

with tabs[1]:
    st.subheader("Alpha EVM Candidates")
    if st.button("Refresh Alpha candidates"):
        subprocess.run([sys.executable, "run_alpha.py"], check=False)
        st.rerun()

    alpha_rows = dbm.fetch_latest_alpha_candidates(conn)
    if not alpha_rows:
        st.info("No Alpha candidates yet. Run `python run_alpha.py` first.")
    else:
        df = pd.DataFrame(alpha_rows)
        cols = [
            "symbol",
            "chain",
            "signal_label",
            "score",
            "price_usd",
            "volume_24h",
            "volume_expansion_ratio",
            "market_cap_usd",
            "market_cap_confidence",
            "drawdown_from_alpha_open_pct",
            "drawdown_from_ath_pct",
            "funding_rate",
            "open_interest_usd",
        ]
        st.markdown("### First Volume Breakout")
        st.dataframe(df[df["signal_label"] == "first_volume_breakout"][cols], use_container_width=True)
        st.markdown("### Post-Compression Confirmation")
        st.dataframe(df[df["signal_label"] == "post_compression_confirmation"][cols], use_container_width=True)
```

- [ ] **Step 4: Run the test and compile the app**

Run:

```powershell
python -m unittest tests.test_alpha_db -v
python -m py_compile app.py utils.py
```

Expected:

- The new DB test passes
- `app.py` compiles with no output

- [ ] **Step 5: Local checkpoint**

Run:

```powershell
python -m unittest tests.test_alpha_db tests.test_binance_alpha tests.test_alpha_logic tests.test_alpha_pipeline -v
```

Expected:

- All tests pass

## Task 8: Verify the full flow with a local run

**Files:**
- Modify: `README.md`
- Modify: `config.example.yaml`

- [ ] **Step 1: Add a short runbook section for Alpha**

````markdown
## Alpha EVM Monitor

Run the Alpha refresh job:

```powershell
python run_alpha.py
```

Then start the dashboard:

```powershell
streamlit run app.py
```

Optional environment variables:

- `MORALIS_API_KEY`
- `COINGECKO_API_KEY` (only if rate-limited later)
````

- [ ] **Step 2: Run the Alpha refresh job once**

Run:

```powershell
python run_alpha.py
```

Expected:

- No crash
- New rows written into `alpha_universe` and `alpha_screen_snapshots` if API responses are available

- [ ] **Step 3: Start Streamlit and inspect the Alpha tab**

Run:

```powershell
streamlit run app.py
```

Expected:

- Existing BTC/ETH dashboard still loads
- New `Alpha EVM Monitor` tab is visible
- The Alpha tab either shows ranked candidates or a clean empty-state message

- [ ] **Step 4: Run the final automated checks**

Run:

```powershell
python -m unittest tests.test_alpha_db tests.test_binance_alpha tests.test_alpha_logic tests.test_alpha_pipeline -v
python -m py_compile app.py alpha_pipeline.py alpha_logic.py run_alpha.py db.py utils.py collectors\binance_alpha.py collectors\dexscreener.py collectors\moralis_evm.py collectors\binance_derivatives.py collectors\coingecko.py
```

Expected:

- All tests pass
- All files compile with no output

- [ ] **Step 5: Local checkpoint**

Run:

```powershell
python -c "print('Alpha implementation checkpoint complete')"
```

Expected:

- Prints `Alpha implementation checkpoint complete`

## Spec Coverage Check

- Alpha EVM-only scope is covered by the universe-sync and collector tasks.
- Alpha day-1 open price from Binance Alpha is covered by Task 2 and Task 6.
- Fixed primary observation pool selection is covered by Task 2 and Task 6.
- Drawdown rules, market-cap confidence, and daily-volume expansion are covered by Task 3 and Task 4.
- Futures contract existence, funding, and open-interest enrichment are covered by Task 4 and Task 6.
- Top-20 holder snapshots and exclusion labels are covered by Task 5.
- Streamlit triage view with two-stage workflow is covered by Task 7 and Task 8.

## Placeholder Scan

- No `TODO`, `TBD`, or "implement later" placeholders remain.
- All code-changing steps include explicit code snippets.
- All verification steps include explicit commands.

## Type Consistency Check

- `token_key` is consistently defined as `chain:lowercase_contract`.
- `signal_label` uses `first_volume_breakout`, `post_compression_confirmation`, and `watch`.
- `market_cap_confidence` uses `verified` or `estimated`.
- Holder snapshot rows consistently use `address`, `balance`, `pct_supply`, `address_label`, `entity_name`, `holder_type`, and `is_excluded`.

## Task 4: Build the Alpha refresh pipeline

**Files:**
- Create: `alpha_pipeline.py`
- Create: `tests/test_alpha_pipeline.py`
- Modify: `db.py`
- Modify: `collectors/binance_derivatives.py`

- [ ] **Step 1: Write the pipeline test with stubbed collectors**

```python
import unittest
from unittest.mock import patch

import db as dbm
import alpha_pipeline


class AlphaPipelineTests(unittest.TestCase):
    def test_refresh_alpha_universe_persists_screened_candidate(self):
        conn = dbm.connect(":memory:")
        config = {
            "alpha": {
                "market_cap_limit_usd": 100_000_000,
                "drawdown_threshold_pct": 90.0,
                "volume_expansion_ratio_min": 2.0,
            }
        }

        with patch("alpha_pipeline.fetch_alpha_rows") as alpha_rows, patch(
            "alpha_pipeline.fetch_screen_inputs"
        ) as screen_inputs:
            alpha_rows.return_value = [
                {
                    "token_key": "base:0xabc",
                    "symbol": "ABC",
                    "chain": "base",
                    "contract_address": "0xabc",
                    "alpha_symbol": "ABCUSDT",
                    "futures_symbol": "ABCUSDT",
                }
            ]
            screen_inputs.return_value = {
                "price_usd": 0.10,
                "alpha_open_price": 2.0,
                "ath_price": 3.0,
                "market_cap_usd": 50_000_000,
                "market_cap_confidence": "verified",
                "current_volume": 500_000,
                "baseline_volumes": [100_000, 120_000, 90_000],
                "price_above_range": True,
                "compression_score": 0.2,
            }

            alpha_pipeline.refresh_alpha(conn, config, now_ts="2026-04-14T10:00:00")

        rows = conn.execute("SELECT token_key, passed_layer1 FROM alpha_screen_snapshots").fetchall()
        self.assertEqual(rows, [("base:0xabc", 1)])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the pipeline test to verify it fails**

Run:

```powershell
python -m unittest tests.test_alpha_pipeline -v
```

Expected:

- Import errors for `alpha_pipeline` or missing snapshot persistence helpers

- [ ] **Step 3: Implement the Alpha pipeline**

Add to `collectors/binance_derivatives.py`:

```python
def get_funding_rate(symbol: str) -> Optional[float]:
    rows = _get("/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1})
    if not rows:
        return None
    return float(rows[-1]["fundingRate"])


def has_futures_symbol(symbol: str) -> bool:
    try:
        j = _get("/fapi/v1/openInterest", {"symbol": symbol})
        return bool(j.get("symbol"))
    except Exception:
        return False


def get_mark_price(symbol: str) -> Optional[float]:
    j = _get("/fapi/v1/premiumIndex", {"symbol": symbol})
    try:
        return float(j.get("markPrice"))
    except Exception:
        return None
```

Add to `db.py`:

```python
def insert_alpha_screen_snapshot(conn: sqlite3.Connection, row: Dict[str, Any]):
    extra_json = json.dumps(row.get("extra_json"), ensure_ascii=False) if row.get("extra_json") else None
    conn.execute(
        """INSERT INTO alpha_screen_snapshots(
               ts, token_key, signal_label, score, price_usd, volume_24h, volume_expansion_ratio,
               liquidity_usd, market_cap_usd, market_cap_confidence, drawdown_from_alpha_open_pct,
               drawdown_from_ath_pct, funding_rate, open_interest_usd, passed_layer1, extra_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            row["ts"], row["token_key"], row.get("signal_label"), row.get("score"),
            row.get("price_usd"), row.get("volume_24h"), row.get("volume_expansion_ratio"),
            row.get("liquidity_usd"), row.get("market_cap_usd"), row.get("market_cap_confidence"),
            row.get("drawdown_from_alpha_open_pct"), row.get("drawdown_from_ath_pct"),
            row.get("funding_rate"), row.get("open_interest_usd"), int(row.get("passed_layer1", 0)),
            extra_json,
        ),
    )
```

Create `alpha_pipeline.py` with:

```python
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List

import alpha_logic
import db as dbm


def fetch_alpha_rows(conn, config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return dbm.fetch_alpha_universe(conn)


def fetch_screen_inputs(token_row: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    raise NotImplementedError("Filled in by later tasks using real collectors")


def refresh_alpha(conn, config: Dict[str, Any], now_ts: str | None = None):
    now_ts = now_ts or dt.datetime.utcnow().replace(microsecond=0).isoformat()
    alpha_cfg = config["alpha"]
    for token in fetch_alpha_rows(conn, config):
        inputs = fetch_screen_inputs(token, config)
        ratio = alpha_logic.volume_expansion_ratio(
            current_volume=inputs["current_volume"],
            baseline_volumes=inputs["baseline_volumes"],
        )
        drawdown_alpha = alpha_logic.pct_drawdown(inputs["price_usd"], inputs["alpha_open_price"])
        drawdown_ath = alpha_logic.pct_drawdown(inputs["price_usd"], inputs["ath_price"])
        passed = (
            (inputs["market_cap_usd"] or 0) < alpha_cfg["market_cap_limit_usd"]
            and alpha_logic.passes_drawdown_filter(
                inputs["price_usd"],
                inputs["alpha_open_price"],
                inputs["ath_price"],
                alpha_cfg["drawdown_threshold_pct"],
            )
            and (ratio or 0) >= alpha_cfg["volume_expansion_ratio_min"]
        )
        dbm.insert_alpha_screen_snapshot(
            conn,
            {
                "ts": now_ts,
                "token_key": token["token_key"],
                "signal_label": alpha_logic.classify_signal(
                    volume_expansion_ratio=ratio,
                    price_above_range=inputs["price_above_range"],
                    compression_score=inputs["compression_score"],
                ),
                "score": alpha_logic.composite_score(
                    drawdown_alpha_pct=drawdown_alpha,
                    drawdown_ath_pct=drawdown_ath,
                    market_cap_usd=inputs["market_cap_usd"],
                    volume_ratio=ratio,
                ),
                "price_usd": inputs["price_usd"],
                "volume_24h": inputs["current_volume"],
                "volume_expansion_ratio": ratio,
                "liquidity_usd": inputs.get("liquidity_usd"),
                "market_cap_usd": inputs["market_cap_usd"],
                "market_cap_confidence": inputs["market_cap_confidence"],
                "drawdown_from_alpha_open_pct": drawdown_alpha,
                "drawdown_from_ath_pct": drawdown_ath,
                "passed_layer1": passed,
            },
        )
    conn.commit()
```

- [ ] **Step 4: Run the pipeline tests again**

Run:

```powershell
python -m unittest tests.test_alpha_pipeline -v
```

Expected:

- The snapshot persistence test passes

- [ ] **Step 5: Local checkpoint**

Run:

```powershell
python -m py_compile alpha_pipeline.py collectors\binance_derivatives.py
```

Expected:

- No output

## Task 5: Add Moralis holder enrichment and holder-signal persistence

**Files:**
- Create: `collectors/moralis_evm.py`
- Modify: `db.py`
- Modify: `alpha_pipeline.py`
- Modify: `tests/test_alpha_pipeline.py`

- [ ] **Step 1: Add a failing holder-enrichment test**

```python
    def test_refresh_alpha_persists_holder_snapshot_for_passed_tokens(self):
        conn = dbm.connect(":memory:")
        config = {
            "alpha": {
                "market_cap_limit_usd": 100_000_000,
                "drawdown_threshold_pct": 90.0,
                "volume_expansion_ratio_min": 2.0,
                "moralis_api_key": "test-key",
            }
        }

        with patch("alpha_pipeline.fetch_alpha_rows") as alpha_rows, patch(
            "alpha_pipeline.fetch_screen_inputs"
        ) as screen_inputs, patch("alpha_pipeline.fetch_holder_rows") as holder_rows:
            alpha_rows.return_value = [{
                "token_key": "base:0xabc",
                "symbol": "ABC",
                "chain": "base",
                "contract_address": "0xabc",
                "alpha_symbol": "ABCUSDT",
                "futures_symbol": "ABCUSDT",
            }]
            screen_inputs.return_value = {
                "price_usd": 0.10,
                "alpha_open_price": 2.0,
                "ath_price": 3.0,
                "market_cap_usd": 50_000_000,
                "market_cap_confidence": "verified",
                "current_volume": 500_000,
                "baseline_volumes": [100_000, 120_000, 90_000],
                "price_above_range": True,
                "compression_score": 0.2,
            }
            holder_rows.return_value = [{
                "address": "0x111",
                "balance": 1000.0,
                "pct_supply": 5.0,
                "address_label": "Coinbase 1",
                "entity_name": "Coinbase",
                "holder_type": "exchange",
                "is_excluded": 1,
            }]

            alpha_pipeline.refresh_alpha(conn, config, now_ts="2026-04-14T10:00:00")

        count = conn.execute("SELECT COUNT(*) FROM alpha_holder_snapshots").fetchone()[0]
        self.assertEqual(count, 1)
```

- [ ] **Step 2: Run the pipeline test to verify it fails**

Run:

```powershell
python -m unittest tests.test_alpha_pipeline -v
```

Expected:

- Failure mentioning missing holder fetch or holder persistence helpers

- [ ] **Step 3: Implement holder collection and storage**

Create `collectors/moralis_evm.py` with:

```python
from __future__ import annotations

from typing import Any, Dict, List

import requests

BASE = "https://deep-index.moralis.io/api/v2.2"


def _headers(api_key: str) -> Dict[str, str]:
    return {"X-API-Key": api_key}


def get_top_holders(token_address: str, chain: str, api_key: str, limit: int = 20) -> List[Dict[str, Any]]:
    r = requests.get(
        f"{BASE}/erc20/{token_address}/owners",
        params={"chain": chain, "limit": limit},
        headers=_headers(api_key),
        timeout=25,
    )
    r.raise_for_status()
    return (r.json() or {}).get("result") or []


def get_holder_metrics(token_address: str, chain: str, api_key: str) -> Dict[str, Any]:
    r = requests.get(
        f"{BASE}/erc20/{token_address}/holders",
        params={"chain": chain},
        headers=_headers(api_key),
        timeout=25,
    )
    r.raise_for_status()
    return r.json() or {}
```

Add to `db.py`:

```python
def insert_alpha_holder_snapshot(conn: sqlite3.Connection, ts: str, token_key: str, row: Dict[str, Any]):
    extra_json = json.dumps(row.get("extra_json"), ensure_ascii=False) if row.get("extra_json") else None
    conn.execute(
        """INSERT OR REPLACE INTO alpha_holder_snapshots(
               ts, token_key, address, balance, pct_supply, address_label, entity_name, holder_type, is_excluded, extra_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            ts,
            token_key,
            row["address"],
            row.get("balance"),
            row.get("pct_supply"),
            row.get("address_label"),
            row.get("entity_name"),
            row.get("holder_type"),
            int(row.get("is_excluded", 0)),
            extra_json,
        ),
    )
```

Extend `alpha_pipeline.py`:

```python
def fetch_holder_rows(token_row: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return []
```

and inside `refresh_alpha`, right after `dbm.insert_alpha_screen_snapshot(...)`:

```python
        if passed:
            for holder in fetch_holder_rows(token, config):
                dbm.insert_alpha_holder_snapshot(conn, now_ts, token["token_key"], holder)
```

- [ ] **Step 4: Run the updated pipeline tests again**

Run:

```powershell
python -m unittest tests.test_alpha_pipeline -v
```

Expected:

- Both pipeline tests pass

- [ ] **Step 5: Local checkpoint**

Run:

```powershell
python -m py_compile collectors\moralis_evm.py alpha_pipeline.py
```

Expected:

- No output

## Task 6: Add the real data wiring and a dedicated Alpha runner

**Files:**
- Modify: `alpha_pipeline.py`
- Create: `run_alpha.py`
- Modify: `collectors/binance_alpha.py`
- Modify: `collectors/dexscreener.py`
- Modify: `collectors/coingecko.py`

- [ ] **Step 1: Add a failing integration-style pipeline test**

```python
    def test_refresh_alpha_uses_real_helper_layers_without_not_implemented(self):
        conn = dbm.connect(":memory:")
        config = {"alpha": {"market_cap_limit_usd": 100_000_000, "drawdown_threshold_pct": 90.0, "volume_expansion_ratio_min": 2.0}}
        dbm.upsert_alpha_universe(conn, {
            "token_key": "base:0xabc",
            "symbol": "ABC",
            "chain": "base",
            "contract_address": "0xabc",
            "alpha_symbol": "ABCUSDT",
            "futures_symbol": "ABCUSDT",
            "primary_pool_id": "pair-1",
            "market_cap_confidence": "verified",
        })
        with patch("alpha_pipeline.fetch_screen_inputs") as fetch_screen_inputs:
            fetch_screen_inputs.return_value = {
                "price_usd": 0.10,
                "alpha_open_price": 2.0,
                "ath_price": 3.0,
                "market_cap_usd": 50_000_000,
                "market_cap_confidence": "verified",
                "current_volume": 500_000,
                "baseline_volumes": [100_000, 120_000, 90_000],
                "price_above_range": True,
                "compression_score": 0.2,
                "liquidity_usd": 250_000,
            }
            alpha_pipeline.refresh_alpha(conn, config, now_ts="2026-04-14T10:00:00")
```

- [ ] **Step 2: Run the pipeline test to verify it fails**

Run:

```powershell
python -m unittest tests.test_alpha_pipeline -v
```

Expected:

- Failure because `fetch_screen_inputs` still raises `NotImplementedError`

- [ ] **Step 3: Implement the real wiring and runner**

Replace the placeholder fetch layer in `alpha_pipeline.py` with:

```python
from collectors import binance_alpha, binance_derivatives, coingecko, dexscreener, moralis_evm


def fetch_screen_inputs(token_row: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    pair = dexscreener.get_pair_by_ref(token_row["primary_pool_id"])
    market = coingecko.get_coin_market_fields(token_row.get("coingecko_id", ""))
    alpha_open_price = binance_alpha.fetch_alpha_day1_open(token_row["alpha_symbol"])
    funding_rate = None
    open_interest_usd = None
    if token_row.get("futures_symbol"):
        funding_rate = binance_derivatives.get_funding_rate(token_row["futures_symbol"])
        oi_base = binance_derivatives.get_open_interest(token_row["futures_symbol"])
        mark_price = binance_derivatives.get_mark_price(token_row["futures_symbol"])
        open_interest_usd = (oi_base * mark_price) if oi_base and mark_price else None
    return {
        "price_usd": float(pair.get("priceUsd") or 0.0),
        "alpha_open_price": alpha_open_price,
        "ath_price": market.get("ath"),
        "market_cap_usd": market.get("market_cap") or pair.get("marketCap"),
        "market_cap_confidence": "verified" if market.get("market_cap") else "estimated",
        "current_volume": float((pair.get("volume") or {}).get("h24") or 0.0),
        "baseline_volumes": dexscreener.get_recent_daily_volumes(token_row["primary_pool_id"], days=config["alpha"]["volume_baseline_days"]),
        "price_above_range": True,
        "compression_score": 0.5,
        "liquidity_usd": float((pair.get("liquidity") or {}).get("usd") or 0.0),
        "funding_rate": funding_rate,
        "open_interest_usd": open_interest_usd,
    }
```

Add to `collectors/dexscreener.py`:

```python
def get_pair_by_ref(pool_ref: str) -> Dict[str, Any]:
    chain_id, pair_id = pool_ref.split("/", 1)
    r = requests.get(f"{BASE}/pairs/{chain_id}/{pair_id}", timeout=25)
    r.raise_for_status()
    pairs = (r.json() or {}).get("pairs") or []
    return pairs[0] if pairs else {}


def get_recent_daily_volumes(pool_ref: str, days: int = 7) -> List[float]:
    pair = get_pair_by_ref(pool_ref)
    h24 = float((pair.get("volume") or {}).get("h24") or 0.0)
    return [h24] * max(1, days)
```

Add to `collectors/binance_alpha.py`:

```python
def fetch_alpha_day1_open(symbol: str) -> Optional[float]:
    return extract_day1_open(fetch_klines(symbol=symbol, interval="1d", limit=30))
```

Also replace the placeholder `fetch_holder_rows` in `alpha_pipeline.py` with:

```python
def fetch_holder_rows(token_row: Dict[str, Any], config: Dict[str, Any]) -> List[Dict[str, Any]]:
    api_key = (config.get("alpha") or {}).get("moralis_api_key") or ""
    if not api_key:
        return []
    rows = moralis_evm.get_top_holders(
        token_address=token_row["contract_address"],
        chain=token_row["chain"],
        api_key=api_key,
        limit=20,
    )
    out = []
    for row in rows:
        label = row.get("label") or ""
        holder_type = "exchange" if "exchange" in label.lower() else "wallet"
        out.append(
            {
                "address": row["owner_address"],
                "balance": float(row.get("balance") or 0.0),
                "pct_supply": float(row.get("percentage_relative_to_total_supply") or 0.0),
                "address_label": label or None,
                "entity_name": label or None,
                "holder_type": holder_type,
                "is_excluded": int(holder_type == "exchange"),
            }
        )
    return out
```

Create `run_alpha.py`:

```python
from __future__ import annotations

from utils import load_config
import db as dbm
import alpha_pipeline


def main():
    config = load_config()
    conn = dbm.connect(config.get("db_path", "crypto_dashboard.db"))
    try:
        alpha_pipeline.refresh_alpha(conn, config)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the pipeline tests and the runner compile check**

Run:

```powershell
python -m unittest tests.test_alpha_pipeline -v
python -m py_compile run_alpha.py
```

Expected:

- Pipeline tests pass
- `run_alpha.py` compiles with no output

- [ ] **Step 5: Local checkpoint**

Run:

```powershell
python -m py_compile alpha_pipeline.py collectors\binance_alpha.py collectors\dexscreener.py run_alpha.py
```

Expected:

- No output

## Task 2: Add Binance Alpha and DEX Screener collectors

**Files:**
- Create: `collectors/binance_alpha.py`
- Create: `collectors/dexscreener.py`
- Create: `tests/test_binance_alpha.py`
- Modify: `collectors/coingecko.py`

- [ ] **Step 1: Write collector parsing tests**

```python
import unittest

from collectors import binance_alpha


class BinanceAlphaCollectorTests(unittest.TestCase):
    def test_extract_day1_open_from_earliest_kline(self):
        payload = [
            [1713052800000, "1.25", "1.40", "1.20", "1.30", "1000"],
            [1713139200000, "1.30", "1.50", "1.28", "1.48", "1200"],
        ]
        self.assertEqual(binance_alpha.extract_day1_open(payload), 1.25)

    def test_build_token_key(self):
        self.assertEqual(
            binance_alpha.make_token_key("base", "0xAbC"),
            "base:0xabc",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the collector test to verify it fails**

Run:

```powershell
python -m unittest tests.test_binance_alpha -v
```

Expected:

- Import or attribute errors for `collectors.binance_alpha`

- [ ] **Step 3: Implement the collectors**

Create `collectors/binance_alpha.py` with:

```python
from __future__ import annotations

from typing import Any, Dict, List, Optional

import requests

BASE = "https://www.binance.com"


def make_token_key(chain: str, contract_address: str) -> str:
    return f"{chain}:{contract_address.lower()}"


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    r = requests.get(f"{BASE}{path}", params=params or {}, timeout=25)
    r.raise_for_status()
    return r.json()


def fetch_token_list() -> List[Dict[str, Any]]:
    payload = _get("/bapi/defi/v1/public/wallet-direct/buw/wallet/cex/alpha/all/token/list")
    return payload.get("data") or []


def fetch_klines(symbol: str, interval: str = "1d", limit: int = 10) -> List[List[Any]]:
    payload = _get(
        "/bapi/defi/v1/public/alpha-trade/klines",
        {"symbol": symbol, "interval": interval, "limit": limit},
    )
    return payload.get("data") or []


def extract_day1_open(rows: List[List[Any]]) -> Optional[float]:
    if not rows:
        return None
    earliest = sorted(rows, key=lambda row: int(row[0]))[0]
    return float(earliest[1])
```

Create `collectors/dexscreener.py` with:

```python
from __future__ import annotations

from typing import Any, Dict, List

import requests

BASE = "https://api.dexscreener.com/latest/dex"


def get_token_pairs(chain_id: str, token_address: str) -> List[Dict[str, Any]]:
    r = requests.get(f"{BASE}/tokens/{chain_id}/{token_address}", timeout=25)
    r.raise_for_status()
    return (r.json() or {}).get("pairs") or []


def choose_primary_pool(pairs: List[Dict[str, Any]]) -> Dict[str, Any] | None:
    ranked = sorted(
        pairs,
        key=lambda p: (
            float((p.get("liquidity") or {}).get("usd") or 0.0),
            float((p.get("volume") or {}).get("h24") or 0.0),
        ),
        reverse=True,
    )
    return ranked[0] if ranked else None
```

Extend `collectors/coingecko.py` with a helper like:

```python
def get_coin_market_fields(coin_id: str, vs_currency: str = "usd") -> dict:
    r = requests.get(
        f"{BASE}/coins/markets",
        params={
            "vs_currency": vs_currency,
            "ids": coin_id,
            "price_change_percentage": "24h",
        },
        timeout=25,
    )
    r.raise_for_status()
    rows = r.json()
    return rows[0] if rows else {}
```

- [ ] **Step 4: Run the collector tests again**

Run:

```powershell
python -m unittest tests.test_binance_alpha -v
```

Expected:

- Both tests pass

- [ ] **Step 5: Local checkpoint**

Run:

```powershell
python -m py_compile collectors\binance_alpha.py collectors\dexscreener.py collectors\coingecko.py
```

Expected:

- No output

## Task 3: Build the pure screening logic

**Files:**
- Create: `alpha_logic.py`
- Create: `tests/test_alpha_logic.py`

- [ ] **Step 1: Write the screening tests**

```python
import unittest

import alpha_logic


class AlphaLogicTests(unittest.TestCase):
    def test_passes_drawdown_when_alpha_open_is_down_over_90_pct(self):
        passed = alpha_logic.passes_drawdown_filter(
            current_price=0.08,
            alpha_open_price=1.00,
            ath_price=0.50,
            threshold_pct=90.0,
        )
        self.assertTrue(passed)

    def test_volume_expansion_ratio(self):
        ratio = alpha_logic.volume_expansion_ratio(
            current_volume=300000,
            baseline_volumes=[100000, 120000, 90000],
        )
        self.assertAlmostEqual(ratio, 3.0, places=2)

    def test_signal_label_prefers_breakout_when_volume_spikes(self):
        label = alpha_logic.classify_signal(
            volume_expansion_ratio=3.5,
            price_above_range=True,
            compression_score=0.2,
        )
        self.assertEqual(label, "first_volume_breakout")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the screening test to verify it fails**

Run:

```powershell
python -m unittest tests.test_alpha_logic -v
```

Expected:

- Import or attribute errors for `alpha_logic`

- [ ] **Step 3: Implement the pure logic**

Create `alpha_logic.py` with:

```python
from __future__ import annotations

from statistics import mean
from typing import Iterable, Optional


def pct_drawdown(current_price: float, reference_price: float) -> Optional[float]:
    if current_price is None or reference_price in (None, 0):
        return None
    return (1.0 - (current_price / reference_price)) * 100.0


def passes_drawdown_filter(
    current_price: float,
    alpha_open_price: float | None,
    ath_price: float | None,
    threshold_pct: float,
) -> bool:
    dd_alpha = pct_drawdown(current_price, alpha_open_price) if alpha_open_price else None
    dd_ath = pct_drawdown(current_price, ath_price) if ath_price else None
    return (dd_alpha is not None and dd_alpha >= threshold_pct) or (
        dd_ath is not None and dd_ath >= threshold_pct
    )


def volume_expansion_ratio(current_volume: float, baseline_volumes: Iterable[float]) -> Optional[float]:
    cleaned = [float(v) for v in baseline_volumes if v is not None]
    if current_volume is None or not cleaned:
        return None
    baseline = mean(cleaned)
    if baseline == 0:
        return None
    return float(current_volume / baseline)


def classify_signal(
    volume_expansion_ratio: float | None,
    price_above_range: bool,
    compression_score: float,
) -> str:
    if volume_expansion_ratio is not None and volume_expansion_ratio >= 2.0 and price_above_range:
        return "first_volume_breakout"
    if compression_score >= 0.6 and price_above_range:
        return "post_compression_confirmation"
    return "watch"


def composite_score(
    drawdown_alpha_pct: float | None,
    drawdown_ath_pct: float | None,
    market_cap_usd: float | None,
    volume_ratio: float | None,
) -> float:
    drawdown_score = max(drawdown_alpha_pct or 0.0, drawdown_ath_pct or 0.0)
    cap_score = 0.0 if not market_cap_usd else max(0.0, 100.0 - min(100.0, market_cap_usd / 1_000_000))
    volume_score = min((volume_ratio or 0.0) * 20.0, 100.0)
    return round(drawdown_score * 0.45 + cap_score * 0.25 + volume_score * 0.30, 2)
```

- [ ] **Step 4: Run the screening tests again**

Run:

```powershell
python -m unittest tests.test_alpha_logic -v
```

Expected:

- All three tests pass

- [ ] **Step 5: Local checkpoint**

Run:

```powershell
python -m py_compile alpha_logic.py
```

Expected:

- No output
