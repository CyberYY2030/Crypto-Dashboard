# Crypto Daily Dashboard (BTC/ETH) — Streamlit + SQLite + Telegram Alert

## What you get (MVP)
- One-page Streamlit dashboard (BTC/ETH):
  - Price cards (24h/7d/30d + range)
  - Realized volatility (7d/30d)
  - Fear & Greed index (latest + history chart)
  - DeFi TVL (DefiLlama /charts)
  - Stablecoin market cap (DefiLlama /stablecoincharts/all)
  - Binance Futures Open Interest (BTCUSDT, ETHUSDT)
- Daily collector (writes to SQLite)
- Telegram alert to a group when **|BTC 24h change| > threshold** (default 7%)

## Security note (important)
**Do NOT hardcode your Telegram bot token in code or commit it to git.**
Use environment variables instead. If you already shared a token publicly, rotate it in BotFather.

## Install (Windows PowerShell)
```powershell
cd crypto_dashboard
# First setup only. Use your installed Python 3.11 explicitly when `python`
# is not available on PATH.
& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -m venv .venv
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt
```

## Configure
Copy `config.example.yaml` to `config.yaml` and edit if needed.

Set Telegram secrets (PowerShell):
```powershell
$env:TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
$env:TELEGRAM_CHAT_ID="-100xxxxxxxxxx"   # group chat id usually starts with -100
```

## Run the daily job (collector + alert)
```powershell
& ".\.venv\Scripts\python.exe" run_daily.py
```

### Macro publish status

Each `run_daily.py` run first records a `running` entry in `collection_runs`.
Only a run with fresh, non-null Binance values for BTC price, BTC 24h change, ETH
price, and ETH 24h change is published as `complete` together with its metrics.
Optional OI, Fear & Greed, TVL, stablecoin, and ETF failures are retained as run
warnings. A failed run rolls back its metrics and the dashboard keeps the most
recent complete run; pre-ledger data is labelled `legacy / unverified`.

`run_daily_fixed.py` remains in place because its historical scheduler use has
not been confirmed. It is not the canonical entrypoint.

## Alpha run contract

`run_alpha.py` publishes only an `alpha_screen_runs` batch marked `complete`.
The dashboard ignores legacy, running, failed, and incomplete Alpha snapshots; use
`run_alpha.py --force` only for an explicit manual refresh. One controlled live
canary with a three-token reference batch ended `incomplete` and published no
screen or holder rows; it is provider/cache evidence, not production validation.

Each screen run takes current price, volume, and liquidity from its persisted fixed
DEX pool through bounded token batches, followed only for omitted persisted pools by
bounded exact-pair batches. It never rotates to a newly discovered pool. A run first
requires 80% current-pool coverage; lower coverage is `incomplete` with no reference
warming, screen, or holder publication. Among usable pools, only tokens not
conservatively excluded by every available inexpensive cap estimate enter the target
set. The listing reference is Binance Web3 DEX's first available daily contract-candle
open, which may be later than the listing UTC day; it is not an Alpha trade open or
first-fill price. CoinGecko context and completed daily OHLCV are cached references
for that target set, with a separate 80% ready gate. Reference attempts are serially
paced at 15.1 seconds by default, using this machine's conservative live baseline
rather than changing GeckoTerminal's published 30 requests/minute limit. A complete run with zero targets
is an explicit “no target candidates” result. `--reference-batch-size 1..30` is a
one-run override for a controlled canary; the pacing change has no live proof.

The Alpha dashboard separates data gaps from ready near misses. Its gate counts are
independent configured-threshold counts, except for the combined All Core Gates count.

## Run the dashboard
```powershell
& ".\.venv\Scripts\python.exe" -m streamlit run app.py
```

## Alpha EVM Monitor
Run the Alpha refresh job:

```powershell
& ".\.venv\Scripts\python.exe" run_alpha.py
```

Then open the dashboard and switch to the `Alpha EVM Monitor` tab:

```powershell
& ".\.venv\Scripts\python.exe" -m streamlit run app.py
```

Optional environment variables:
- `MORALIS_API_KEY`
- `COINGECKO_API_KEY` (only if rate-limited later)

## Schedule at 08:00 Beijing time
If your Windows timezone is China Standard Time (UTC+8), schedule `run_daily.py` daily at 08:00.

If your Windows timezone is NOT UTC+8, schedule it at the equivalent local time.
The script uses `Asia/Shanghai` to decide the "daily" date boundary.

### Task Scheduler command
Program/script:
- `powershell.exe`

Arguments:
- `-ExecutionPolicy Bypass -NoProfile -Command "cd <ABS_PATH> ; .\.venv\Scripts\python.exe run_daily.py"`

Replace `<ABS_PATH>` with your folder absolute path.


## V2 changes (requested)
- OI displayed as **billion USD** (converted from Binance OI * spot price).
- Stablecoins extraction made more robust for DefiLlama schema variants.
- ahr999 computed locally using original definition (GMA200 + coin age valuation).
- Charts replaced: Fear & Greed, BTC RV30, DeFi TVL, Stablecoin Mcap (last ~3 months).


## Backfill logic (new)
`& ".\.venv\Scripts\python.exe" run_daily.py` will:
1) Backfill ~1 year of Fear&Greed / TVL / Stablecoins and ~650d BTC prices (for ahr999 + RV)
2) Compute derived metrics locally (RV7/RV30, 7D/30D returns, ahr999)
3) Collect today's snapshot (24h change, Binance OI, optional ETF metrics)
4) Send Telegram alert if |BTC 24h %| > threshold

## Optional: SoSoValue ETF Metrics
SoSoValue "Get current ETF data metrics" requires an API key via header `x-soso-api-key`.
Set environment variable:
```powershell
$env:SOSO_API_KEY="YOUR_x-soso-api-key"
```
Docs: https://sosovalue.gitbook.io/soso-value-api-doc/api-document/get-current-etf-data-metrics


## Price history source
This build uses Binance Spot `/api/v3/klines` for daily price history (no API key) to avoid CoinGecko 401 issues.


## DefiLlama Stablecoins 404 fix
Stablecoin history is served from `https://stablecoins.llama.fi/stablecoincharts/all` (note the `/stablecoins/` prefix and different subdomain). This build uses that endpoint.


## ETF display
Dashboard shows ETF Net Inflow and Total Net Assets (TNA). Historical series stored under metrics `*_etf_totalNetInflow_usd` and `*_etf_total_net_assets_usd`.


## SoSoValue API key troubleshooting (Windows)
- `$env:SOSO_API_KEY="..."` only sets the variable for the **current PowerShell session**.
  If you open a new terminal window, it will be empty again.
- To set it persistently for your user:
  `setx SOSO_API_KEY "YOUR_KEY"`
  Then open a **new** PowerShell window.
- For Task Scheduler runs, environment variables depend on the task context.
  Alternative: put the key into `config.yaml` under:
  ```yaml
  sosovalue:
    api_key: "YOUR_KEY"
  ```
- Logs: `logs/sosovalue.log` will record whether ETF requests ran and the first ~300 chars of responses.



## SoSoValue SSL CERTIFICATE_VERIFY_FAILED (Windows)
If you see:
`SSLCertVerificationError: unable to get local issuer certificate`
it means your Python/Windows trust store doesn't have the required CA chain.

This build forces requests to use `certifi` CA bundle by default.

Steps:
1) Reinstall deps:
   `& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt`
2) Retry:
   `& ".\.venv\Scripts\python.exe" run_daily.py`

If you're behind a corporate proxy with custom CA:
- Set `REQUESTS_CA_BUNDLE` to your proxy CA pem file.

Last-resort (NOT recommended): disable verification for SoSoValue only:
`$env:SOSO_SSL_NO_VERIFY="1"`
