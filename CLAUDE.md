# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A swing trading agent that runs a daily multi-agent AI pipeline to scan a custom watchlist of ~82 tickers (contex/watchlist.json), perform fundamental and technical analysis, assess news sentiment, and generate ranked trade recommendations (long and short) with full risk parameters. Results are delivered via text/JSON reports and optionally via Telegram.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run the full morning pipeline immediately
python main.py --session morning

# Run the evening pipeline
python main.py --session evening


# Skip Claude API calls (test data pipeline only)
python main.py --dry-run

# Run end-to-end test (real Claude API, mocked market data)
python main.py --test

# Run all tests
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_indicators.py -v

# Start TradingView webhook server (requires ngrok to expose publicly)
python main.py --webhook

# Install Windows Task Scheduler tasks (morning 15:35 + evening 20:30, weekdays)
# Must run as Administrator
python main.py --setup-scheduler

# Run on schedule (blocking loop, alternative to Task Scheduler)
python main.py --schedule
```

## Environment variables (.env)

```
ANTHROPIC_API_KEY=sk-ant-...
PORTFOLIO_VALUE=10000
MIN_RISK_PER_TRADE=500
MAX_RISK_PER_TRADE=600
RUN_TIME=15:35
RUN_TIME_EVENING=20:30
ALPACA_API_KEY=...
ALPACA_API_SECRET=...
TELEGRAM_BOT_TOKEN=        # optional
TELEGRAM_CHAT_ID=          # optional
WEBHOOK_PORT=5000          # optional
WEBHOOK_SECRET=            # optional
```

## Architecture

The pipeline runs sequentially through 6 agents, each feeding the next:

```
MarketScanner → FundamentalAnalyst → TechnicalAnalyst → NewsSentimentAnalyst → RiskManager → ReportWriter
```

**[agents/orchestrator.py](agents/orchestrator.py)** — `TradingOrchestrator.run_daily_pipeline()` drives the whole flow. Each phase is wrapped in a try/except so a failure in one phase doesn't abort the run (except scan, which is fatal). The composite score is computed in `_merge_and_rank()` using weights from `config.py:SCORE_WEIGHTS` (scan 15%, fundamental 15%, TA 35%, sentiment 15%, risk 20%).

**[agents/base_agent.py](agents/base_agent.py)** — All agents extend `BaseAgent`. Key methods:
- `_call_claude()` — raw text call with retry/backoff for rate limits and overload
- `_call_claude_json()` — JSON call with 3-attempt retry and markdown fence stripping
- System prompts use `cache_control: ephemeral` for prompt caching

**[agents/market_scanner.py](agents/market_scanner.py)** — Downloads quotes for the full universe in batches of 50 via yfinance, applies price/volume filters, scores candidates by momentum signals, then asks Claude to prioritize the top 30 (accounting for current portfolio sector exposure). Returns up to 50 candidates.

**[agents/fundamental_analyst.py](agents/fundamental_analyst.py)** — First filter and discovery phase. Fetches fundamental data from Finviz (earnings date, short float, analyst recommendations, insider/institutional transactions, target price, leverage). Blocks candidates with earnings ≤3 days away. Scores 0–10 and also runs a Finviz screener to discover new candidates not in the watchlist (long: Strong Buy + positive insider; short: high short float + weekly decline).

**[agents/technical_analyst.py](agents/technical_analyst.py)** — Fetches 90 days OHLCV per ticker, runs Python-computed indicators, then asks Claude for pattern detection and ta_score. For long setups, the final score averages Python score and Claude score; for short setups, Claude score is used directly.

**[agents/risk_manager.py](agents/risk_manager.py)** — Calculates position sizing (fixed dollar risk per trade), stop-loss via ATR multiplier, and two profit targets. Enforces `MIN_RR_RATIO` minimum risk/reward.

**[data/market_data.py](data/market_data.py)** — `MarketDataFetcher` handles all market data. Alpaca Markets API is the primary source (batch quotes, OHLCV, crypto); yfinance is the fallback and is used exclusively for ^VIX. Ticker universe: if `contex/watchlist.json` exists with a `tickers` array, it's used exclusively; otherwise falls back to S&P 500 + NDX 100 scraped from Wikipedia (cached in `contex/ticker_universe_*.json`, refreshed every 7 days).

**[data/indicators.py](data/indicators.py)** — Pure pandas/numpy computation of RSI, MACD, EMAs, Bollinger Bands, ATR, ADX, support/resistance levels, and a Python-only TA score.

**[utils/context_manager.py](utils/context_manager.py)** — Reads/writes JSON state files in `contex/`. Uses atomic writes (write to `.tmp`, then `os.replace`). Key files:
- `contex/portfolio.json` — current holdings; scanner uses this to avoid sector concentration
- `contex/daily_state_YYYY-MM-DD[_evening].json` — persisted pipeline output used as context for the next run
- `contex/watchlist.json` — optional custom ticker universe override

**[models/schemas.py](models/schemas.py)** — All data structures are `@dataclass` with `to_dict()` / `from_dict()` for JSON serialization. The scoring chain: `ScanCandidate.initial_score` → `TAResult.ta_score` → `SentimentResult.sentiment_score_normalized` → `RiskResult.risk_score` → `FinalCandidate.composite_score`.

**[webhook_server.py](webhook_server.py)** — Flask server that receives TradingView alerts (`POST /webhook`) and runs a single-ticker pipeline in a background thread. Deduplicates concurrent alerts for the same ticker.

## Scoring thresholds

| composite_score | recommendation |
|---|---|
| ≥ 7.5 | STRONG BUY |
| ≥ 6.0 | BUY |
| < 6.0 | WATCH |

## Output files

Reports are written to `output/report_YYYY-MM-DD[_evening].{txt,json}`. Logs go to `output/logs/trading_agent.log`. Old state files are cleaned up after 30 days.

## Broker context (embedded in scanner prompt)

The scanner system prompt encodes two brokers: Broker 1 (EUR, long-only, European+US) and Broker 2 (USD, long+short, NYSE/NASDAQ only). Short candidates are only routed to Broker 2. This context lives in `agents/market_scanner.py:SCANNER_SYSTEM` and may need updating when capital availability changes.

Broker 3 (FPMTrading, CFDs micro-lots — gold, silver, indices) is operated **manually**. The pipeline does not generate recommendations or position sizing for Broker 3.
