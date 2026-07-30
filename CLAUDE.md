# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Persistent operator notes (READ)

Claude's cross-session working memory lives in **[contex/notas_claude.md](contex/notas_claude.md)** — read it each session. It holds durable workflows (e.g. the "cierre del día" close routine), the broker mapping, and setup gotchas. When something important should persist across sessions, append it there (Spanish, dated, concise). This is the reliable manual memory; claude-mem/MEM is only an automatic background layer.

## Strategy & risk policy (READ FIRST for any change to scoring/sizing/exposure)

The trading strategy and risk policy are defined explicitly in **[docs/estrategia_riesgo.md](docs/estrategia_riesgo.md)** — the operator's decisions, not model defaults. Before changing scoring, position sizing, exposure, or concentration, read it. `PORTFOLIO_VALUE` is the **real combined capital of broker_1 + broker_2 (≈€6,400 as of 2026-07-20)**, read **dynamically** from the broker balances in `contex/portfolio.json` each run (`config._compute_portfolio_value`, USD~EUR at parity); broker_3 (manual CFDs) is excluded. Core rules: momentum/high-beta tilt with a **dynamic high-beta exposure cap by regime** (R1), **per-sub-theme concentration limit** to avoid correlated blow-ups like the 2026-06-05 cluster (R2), a **dynamic per-trade risk profile, inverse to VIX (1–3%)** (R3, redefined 2026-06-24 — replaces the old fixed half-size), and an **aggregate portfolio risk cap (10%)** (R6, the real brake on correlated clusters). R1/R2/R3/R6 are **wired in**: parameters in `config.py` §4, tier/sub-theme taxonomy + rule logic in `utils/risk_policy.py` (`risk_pct_for_regime` for R3, `open_position_risk` for R6), applied in `risk_manager.py` (R3 dynamic sizing) and `orchestrator._apply_exposure_caps` (R1/R2/R6 demotion to WATCH). R4 (shorts) lives in `orchestrator._merge_and_rank`. R5 (entry quality — "require strength, don't buy weakness": longs weaker than 0.5 ATR over EMA9 use a stop-entry at the strength threshold instead of market; calibrated by `backtest_entry_quality.py`, which falsified the earlier "don't chase extension" hypothesis — extension isn't the problem, weakness is) lives in `risk_manager.py`. **R7 (re-entry cooldown after a recent loss, new 2026-07-14)**: a ticker that closed at a loss (net > `RECENT_LOSS_MIN_ABS`) within the last `RECENT_LOSS_COOLDOWN_DAYS` calendar days is demoted to WATCH in the **same direction** — `recent_loss_cooldown()` in `utils/risk_policy.py`, applied in `orchestrator._apply_recent_loss_cooldown` (before R1/R2/R6). Motivated by a jul-2026 selection analysis: the engine kept re-recommending just-stopped names (GRAB ×3, MRVL, INTC) and `composite_score` did not separate winners from losers. It's a prototype — window/threshold pending calibration. **R8 (bullish-catalyst guard for shorts, new 2026-07-30)**: a short is demoted to WATCH when the sentiment analyst finds a recent catalyst (`catalyst_found=True`) with `sentiment_score_normalized >= SHORT_BULLISH_CATALYST_MIN` (7.5) — `short_bullish_catalyst_guard()` in `utils/risk_policy.py`, applied in `orchestrator._merge_and_rank` ahead of the other R4 short guards. Motivated by BE (2026-07-29): a short was opened one day after an earnings beat with raised guidance (sentiment_score_normalized 8.9); the sentiment agent had flagged the catalyst but nothing vetoed the trade, and the resulting squeeze (+25% in 24h) blew through the stop with heavy slippage. Fixed alongside it: `FundamentalAnalyst._parse_earnings_days()` mis-parsed a same-week-past earnings date as ~364 days away (a date-rollover bug), silently defeating any earnings-proximity read — now picks whichever year interpretation (prior/current/next) is closest to today. Tests in `tests/test_earnings_parsing.py` and `tests/test_risk_policy.py`. The tier taxonomy is an editable seed — review it periodically. Do not silently override these.

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

**[data/market_data.py](data/market_data.py)** — `MarketDataFetcher` handles all market data. Alpaca Markets API is the primary source (batch quotes, OHLCV, crypto); yfinance is the fallback and is used exclusively for ^VIX. Ticker universe: if `contex/watchlist.json` exists with a `tickers` array, it's used exclusively; otherwise falls back to S&P 500 + NDX 100 scraped from Wikipedia (cached in `contex/ticker_universe_*.json`, refreshed every 7 days). Both `StockBarsRequest` calls pass `adjustment=Adjustment.ALL` (fixed 2026-07-30) — without it Alpaca serves split-unadjusted bars, and any ticker that split within the lookback window gets a price-cliff discontinuity that corrupts every derived indicator (EMAs, high_52w, Bollinger, support/resistance). Caught via CRWD (4:1 split 2026-07-02): `high_52w` read 785.59 instead of the real 217.50.

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
