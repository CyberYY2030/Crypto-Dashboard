# Binance Alpha EVM Monitor Design

Date: 2026-04-14

## Goal

Extend the current crypto dashboard with a new Binance Alpha EVM token monitor focused on finding early abnormal moves.

The first version should prioritize tokens that match this profile:

- Binance Alpha token
- EVM token only
- Binance futures contract exists
- Circulating market cap under 100M USD
- Price is still deeply below Alpha day-1 open or all-time high
- Daily volume begins to expand
- On-chain large-holder behavior starts to improve

The dashboard should help answer one practical question:

Which Alpha EVM tokens are still deeply depressed, small enough to move, supported by Binance futures, and just starting to show early recovery signals?

## Non-goals

- Do not monitor non-EVM chains in V1.
- Do not build a full real-time chain indexer in V1.
- Do not fetch holder changes for every Alpha token continuously.
- Do not depend on Binance spot listing, because some Alpha tokens only trade on-chain.

## Core Design Choice

V1 uses a two-layer pipeline:

1. Cheap and broad screening on Alpha EVM tokens using on-chain market data plus Binance futures availability.
2. Expensive and deep enrichment only for tokens that pass the first layer.

This keeps the system practical, reduces data overload, and matches the target use case of finding abnormal moves at an early stage.

## Data Source Strategy

The system should not force a single source for every field. Different fields have different best sources.

### 1. Alpha universe and Alpha open price

Use Binance Alpha data for:

- Alpha token list
- Alpha symbol mapping
- Alpha K-line history
- Alpha day-1 open price

Rule:

- Alpha day-1 open price must come from Binance Alpha's earliest available K-line.
- If Alpha history is missing for a small number of tokens, mark the token as incomplete and exclude it from the main candidate pool until a fallback rule is explicitly added later.

### 2. Price, volume, liquidity, and trend trigger

Use an on-chain pool source as the main trigger source.

Preferred source:

- DEX Screener or GeckoTerminal

Rule:

- Each token must be assigned one fixed primary observation pool.
- Screening price and volume must come from that fixed pool, not from a rotating "best pool" choice.

Reason:

- This monitor is looking for early abnormal moves, so trigger data should reflect the actual on-chain pool where the move happens.
- Aggregated cross-exchange price can dilute early signals.

### 3. Circulating market cap

Use two trust levels:

- High-confidence market cap: verified circulating market cap from a trusted aggregator such as CoinGecko
- Estimated market cap: fallback estimate from DEX Screener or similar source

Rule:

- Verified circulating market cap is preferred.
- If only an estimate is available, the token may still enter the watchlist, but must be labeled as low-confidence market cap.

Reason:

- Small-cap early tokens often do not have a clean, fully verified circulating market cap.
- Rejecting all such tokens would likely remove useful opportunities.

### 4. All-time high

Use full-market all-time high for V1.

Rule:

- Historical drawdown from all-time high is based on the token's full-market all-time high, not only Alpha or only the observed pool.

### 5. Futures data

Use Binance futures data for:

- Contract existence
- Funding rate
- Open interest
- Other futures-side confirmation fields added later

Rule:

- Futures contract existence is a hard filter.
- If Binance futures data is temporarily unavailable, keep the token out of the final candidate pool for that run.

### 6. Top-20 holder behavior

Use on-chain holder snapshots only for tokens that survive layer 1.

Rule:

- Do not fetch top-holder data for the full Alpha universe.
- Holder behavior is an enrichment layer, not a full-universe trigger in V1.

## Universe Definition

The monitored universe is:

- Binance Alpha tokens
- EVM only

Each token record should include:

- Token name
- Token symbol
- Chain
- Contract address
- Alpha symbol
- Alpha listing timestamp if available
- Binance futures symbol if one exists
- Primary observation pool identifier
- Market-cap confidence flag

## Primary Observation Pool Selection

Every token needs one fixed on-chain pool for price and daily-volume screening.

V1 pool selection rule should prefer:

1. Highest sustained liquidity among active pools
2. Healthy recent trading activity
3. Stable quote asset
4. Avoid obviously broken or dead pools

Once selected, that pool should remain fixed unless it becomes clearly invalid.

The system should store:

- Pool id
- DEX name
- Chain
- Quote token
- Last validation timestamp

## Layer 1 Screening

Layer 1 is the broad screening stage.

### Hard filters

A token must satisfy all of the following:

- Token is in Binance Alpha
- Token is on an EVM chain
- Binance futures contract exists
- Circulating market cap is under 100M USD
- Alpha day-1 open price exists
- Primary observation pool exists

### Deep-drawdown trigger

At least one of these must be true:

- Drawdown from Alpha day-1 open is greater than 90%
- Drawdown from full-market all-time high is greater than 90%

Important:

- Both values should be shown in the dashboard even if only one was used to pass the filter.

### Daily-volume trigger

Use daily on-chain volume expansion from the primary observation pool.

V1 rule:

- Compare current daily volume against a recent baseline window
- Only keep tokens showing clear daily volume expansion

Exact numeric threshold will be finalized during implementation, but the rule must be configurable.

### Supporting fields shown in layer 1 results

Each screened token should display at least:

- Price
- Daily volume
- Daily volume expansion ratio
- Liquidity
- Verified or estimated circulating market cap
- Drawdown from Alpha day-1 open
- Drawdown from all-time high
- Futures contract existence

### Ranking approach

Do not rank only by one field.

Use a weighted score with heavier emphasis on:

- Deep drawdown
- Smaller market cap
- Daily volume expansion
- Futures availability

The output should be a ranked candidate list, not a flat pass/fail table.

## Signal Classification

Tokens passing layer 1 should be split into two labels:

### 1. First volume breakout

Meaning:

- Daily volume expands sharply
- Price starts breaking above a short-term resistance or range
- Intended to capture earlier abnormal movement

### 2. Post-compression trend confirmation

Meaning:

- Token spent a period in reduced activity
- Price structure begins to recover
- Volume improves in a steadier way
- Intended to capture a more confirmed move

The dashboard should keep both labels visible rather than collapsing them into one score.

## Layer 2 Enrichment

Layer 2 runs only for tokens that pass layer 1.

### Futures enrichment

Fetch and store:

- Latest funding rate
- Funding-rate history if needed for context
- Latest open interest
- Open-interest change if available

Purpose:

- Help distinguish weak dead-cat moves from moves that are actually attracting derivatives participation

### Holder enrichment

Fetch and store top-20 real on-chain holder snapshots.

This should not be just a ranking table. It should be converted into behavior signals.

## Top-20 Holder Monitoring Design

### Snapshot model

For each selected token, record periodic snapshots of:

- Current top-20 holder addresses
- Balance per address
- Share of circulating supply if possible

### Address classes to label separately

The following should be labeled and excluded from "real whale behavior" interpretation where possible:

- Burn addresses
- Liquidity-pool addresses
- Bridge addresses
- Treasury or vesting contracts
- Clearly identified exchange deposit or omnibus wallets

### Derived holder signals

The dashboard should show these derived signals:

- Top-20 net accumulation over 1d / 3d / 7d
- Top-5 concentration change
- Top-10 concentration change
- New address entering top 20
- Existing top-20 address dropping out
- Net increase or decrease among real holder addresses only

### Interpretation layer

V1 should classify holder behavior into:

- Bullish holder change
- Bearish holder change
- Structural reshuffle

Definitions:

- Bullish holder change: top-20 net accumulation improves while price has not fully expanded yet
- Bearish holder change: top-20 net reduction appears while price still looks superficially stable
- Structural reshuffle: rankings change materially even if aggregate top-20 balance is stable

## Dashboard Layout

The dashboard should be optimized for triage first and details second.

### 1. Candidate overview

Primary table sorted by score.

Each token row should show:

- Token
- Chain
- Futures symbol
- Market cap and confidence flag
- Drawdown from Alpha day-1 open
- Drawdown from all-time high
- Daily volume
- Daily volume expansion ratio
- Liquidity
- Funding rate
- Open interest
- Top-20 net accumulation summary
- Signal label

### 2. Two signal sections

Separate sections for:

- First volume breakout
- Post-compression trend confirmation

This makes it easy to distinguish early setups from steadier confirmations.

### 3. Token detail view

Detailed view should include:

- Price behavior
- Volume behavior
- Drawdown from Alpha day-1 open
- Drawdown from all-time high
- Funding rate and open interest
- Top-20 holder changes
- New addresses entering top 20
- Concentration changes
- Data-confidence notes

## Run Cadence

Use different frequencies for different cost levels.

### Daily or low-frequency jobs

Run once per day:

- Refresh Alpha token universe
- Refresh token mappings
- Refresh futures availability mapping
- Revalidate primary observation pools when needed

### Medium-frequency jobs

Run every 15 to 30 minutes:

- Layer 1 screening data
- On-chain price and volume
- Liquidity snapshot
- Market-cap refresh if available
- Funding rate and open interest for current candidates

### Heavy jobs

Run only for selected candidates:

- Top-20 holder snapshots

V1 recommendation:

- Run holder snapshots 1 to 4 times per day depending on cost

## Alerts

Alerts should remain sparse and high-signal.

### Alert types

1. New candidate alert

- Token passes layer 1 for the first time

2. Signal-upgrade alert

- Token moves from watch state into one of the two main signal classes
- Or moves from early breakout into stronger confirmation

3. Holder-change alert

- Top-20 net accumulation increases materially
- Or multiple new addresses enter top 20

### Alert philosophy

Do not alert on every metric change.

Only alert when a token becomes meaningfully more interesting to inspect.

## Storage Direction

The current project stores broad metrics in SQLite. V1 should extend that pattern rather than replacing it.

Recommended storage groups:

- Alpha universe table
- Observation-pool mapping table
- Alpha price reference table
- Screening snapshot table
- Futures enrichment table
- Holder snapshot table
- Derived signal table
- Alert history table

The exact schema can be finalized in the implementation plan.

## Data Confidence Rules

The UI should clearly distinguish between:

- Verified circulating market cap
- Estimated circulating market cap
- Missing Alpha open price
- Missing all-time high
- Missing holder labels for known system addresses

If a token relies on estimated market cap, that should be visible in the overview table and detail page.

## Success Criteria

The design is successful if V1 can:

- Monitor Binance Alpha EVM tokens without relying on Binance spot listing
- Screen deeply depressed small-cap tokens with Binance futures support
- Highlight daily-volume expansion from a fixed on-chain pool
- Show both Alpha-open drawdown and all-time-high drawdown
- Split candidates into early-breakout and trend-confirmation groups
- Add top-20 holder behavior only for shortlisted tokens
- Keep alert volume manageable

## Open Implementation Notes

- Daily-volume expansion thresholds should be configurable, not hard-coded into the design
- Exact holder-snapshot frequency should be chosen based on real API cost during implementation
- The first implementation should bias toward reliability and clarity over perfect completeness

## External References

- DEX Screener API: https://docs.dexscreener.com/api/reference
- DEX Screener market-cap notes: https://docs.dexscreener.com/token-listing
- CoinGecko on-chain price reference: https://docs.coingecko.com/reference/onchain-simple-price
- CoinGecko markets reference: https://docs.coingecko.com/reference/coins-markets
- CoinGecko tickers reference: https://docs.coingecko.com/reference/coins-id-tickers
- GeckoTerminal API guide: https://apiguide.geckoterminal.com/
- Binance Alpha overview: https://www.binance.com/en/skills/detail/binance/alpha
