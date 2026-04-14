# Crypto Daily Dashboard

Streamlit dashboard for BTC/ETH market indicators with a local SQLite store and an optional Telegram alert.

## Features
- BTC / ETH price cards with 24h, 7d, and 30d change
- BTC / ETH realized volatility
- Fear & Greed history
- DeFi TVL and stablecoin market cap
- Binance futures open interest
- Optional SoSoValue ETF metrics
- Daily collector that backfills history into SQLite

## Repository Safety
This repository is prepared for public GitHub upload.

- `crypto_dashboard.db` is intentionally excluded from version control.
- `config.yaml` is intentionally excluded from version control.
- Secrets must be provided through environment variables or your local `config.yaml`.
- If you previously exposed a real API key, rotate it before publishing.

## Quick Start
### 1. Clone and install
```bash
git clone https://github.com/CyberYY2030/Crypto-Dashboard.git
cd Crypto-Dashboard
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell:
```powershell
git clone https://github.com/CyberYY2030/Crypto-Dashboard.git
cd Crypto-Dashboard
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Create local config
```bash
cp config.example.yaml config.yaml
```

Windows PowerShell:
```powershell
Copy-Item config.example.yaml config.yaml
```

You can keep `config.yaml` unchanged for the default data sources. The app will create `crypto_dashboard.db` locally on first run.

### 3. Optional secrets
Telegram alert:
```bash
export TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
export TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
```

Optional SoSoValue ETF metrics:
```bash
export SOSO_API_KEY="YOUR_SOSO_API_KEY"
```

Windows PowerShell:
```powershell
$env:TELEGRAM_BOT_TOKEN="YOUR_TOKEN"
$env:TELEGRAM_CHAT_ID="-100xxxxxxxxxx"
$env:SOSO_API_KEY="YOUR_SOSO_API_KEY"
```

You can also put the optional SoSoValue key into your local `config.yaml`:
```yaml
sosovalue:
  api_key: "YOUR_SOSO_API_KEY"
```

Do not commit `config.yaml`.

## First Run
Run the collector once to backfill data and create the local SQLite database:
```bash
python run_daily.py
```

Then start the dashboard:
```bash
streamlit run app.py
```

If no data is available yet, the dashboard will prompt you to run `python run_daily.py`.

## What `run_daily.py` Does
`python run_daily.py` will:
1. Backfill BTC/ETH price history from Binance Spot.
2. Backfill Fear & Greed, DeFi TVL, and stablecoin history.
3. Compute derived metrics such as 7d/30d returns, RV7/RV30, and ahr999.
4. Collect the latest daily snapshot.
5. Send a Telegram alert if the BTC 24h move exceeds the configured threshold.

## Data Sources
- Binance Spot: daily price history and 24h ticker
- Binance Futures: open interest
- Alternative.me: Fear & Greed index
- DefiLlama: DeFi TVL and stablecoin market cap
- SoSoValue: optional ETF metrics

## Scheduling
Schedule `python run_daily.py` once per day. The default date boundary uses `Asia/Shanghai`.

Example Windows Task Scheduler command:
- Program/script: `powershell.exe`
- Arguments: `-ExecutionPolicy Bypass -NoProfile -Command "cd <ABS_PATH> ; .\.venv\Scripts\python.exe run_daily.py"`

Replace `<ABS_PATH>` with your local project path.

## Troubleshooting
### SoSoValue SSL errors
If you hit `SSLCertVerificationError`, reinstall dependencies first:
```bash
pip install -r requirements.txt
```

If you are behind a proxy with a custom CA, set:
```bash
export REQUESTS_CA_BUNDLE="/path/to/your-ca.pem"
```

Last resort only:
```bash
export SOSO_SSL_NO_VERIFY="1"
```

### Empty dashboard after clone
This is expected until you run:
```bash
python run_daily.py
```
