import json
from datetime import datetime

import anthropic

from agents.base_agent import BaseAgent
from config import (
    MIN_PRICE, MAX_PRICE, MIN_AVG_VOLUME, MIN_MARKET_CAP,
    VOLUME_SPIKE_THRESHOLD, NEAR_52W_HIGH_PCT, SCAN_MAX_CANDIDATES, TA_TOP_N,
)
from data.market_data import MarketDataFetcher
from models.schemas import MarketConditions, ScanCandidate, ScanResult


SCANNER_SYSTEM = """You are a professional swing trading analyst. Your job is to prioritize a list of stock scan candidates for further analysis.

Swing trading involves holding positions for 2-10 days, capturing short-to-medium term price moves.

BROKER CAPABILITIES:
- Broker 1 (EUR, limited catalog): Long only. European and US stocks. Currently has €1,677 available.
- Broker 2 (USD, US stocks only): Long AND short positions available. NYSE/NASDAQ only. Currently fully deployed (no funds available until existing positions are closed).

When prioritizing, identify each candidate as:
- "long" — bullish setup, can be executed in Broker 1 or Broker 2
- "short" — bearish setup, can ONLY be executed in Broker 2 (when funds become available)

Prioritize in this order:
1. Volume confirmation (volume spikes often precede big moves)
2. Price proximity to breakout/breakdown levels
3. Sector momentum and rotation
4. Chart structure clarity
5. Market cap and liquidity

For SHORT candidates: look for stocks with deteriorating momentum, high volume distribution, breaks below key moving averages, or bearish patterns (head & shoulders, distribution, failed breakouts).

Return a JSON object with a "prioritized_tickers" array (list of ticker strings, most promising first),
a "directions" object mapping each ticker to "long" or "short",
and a "notes" string explaining your top picks."""


class MarketScanner(BaseAgent):
    def __init__(self, client: anthropic.Anthropic, data_fetcher: MarketDataFetcher):
        super().__init__(client)
        self.data_fetcher = data_fetcher

    def run(self, previous_context: dict = None, portfolio: dict = None) -> ScanResult:
        today = datetime.now().strftime("%Y-%m-%d")
        self.logger.info("Starting market scan...")

        market = self.data_fetcher.get_market_overview()
        self.logger.info(f"Market: SPY={market.spy_trend}, VIX={market.vix_level}, Regime={market.regime}")

        universe = self.data_fetcher.get_universe()
        self.logger.info(f"Downloading quotes for {len(universe)} tickers...")
        quotes = self.data_fetcher.fetch_batch_quotes(universe)

        candidates = self._apply_basic_filters(quotes)
        self.logger.info(f"After filters: {len(candidates)} candidates")

        candidates = self._score_candidates(candidates)
        candidates.sort(key=lambda c: c.initial_score, reverse=True)
        top_candidates = candidates[:80]

        # Refresh today's prices only for top candidates to save time
        self.logger.info(f"Refreshing today's prices for top {len(top_candidates)} candidates...")
        top_tickers = [c.ticker for c in top_candidates]
        today_quotes = self.data_fetcher.refresh_daily_quotes(top_tickers)
        for ticker, today_data in today_quotes.items():
            for c in top_candidates:
                if c.ticker == ticker:
                    c.price = round(today_data["price"], 2)
                    break

        prioritized = self._ask_claude_to_prioritize(top_candidates, market, previous_context, portfolio)

        ordered = []
        ticker_map = {c.ticker: c for c in top_candidates}
        for t in prioritized:
            if t in ticker_map:
                ordered.append(ticker_map[t])
        for c in top_candidates:
            if c.ticker not in [x.ticker for x in ordered]:
                ordered.append(c)

        final = ordered[:SCAN_MAX_CANDIDATES]

        return ScanResult(
            date=today,
            candidates=final,
            total_screened=len(quotes),
            market_conditions=market,
            scan_notes=f"Screened {len(quotes)} tickers, filtered to {len(candidates)}, Claude prioritized top {len(final)}",
        )

    def _apply_basic_filters(self, quotes: dict) -> list:
        candidates = []
        for ticker, q in quotes.items():
            price = q.get("price", 0)
            avg_vol = q.get("avg_vol_20d", 0)
            if not (MIN_PRICE <= price <= MAX_PRICE):
                continue
            if avg_vol < MIN_AVG_VOLUME:
                continue

            signals = []
            vol_ratio = q["volume"] / avg_vol if avg_vol > 0 else 1.0
            if vol_ratio >= VOLUME_SPIKE_THRESHOLD:
                signals.append("volume_spike")

            high_52w = q.get("high_52w", price)
            if high_52w > 0 and price >= high_52w * NEAR_52W_HIGH_PCT:
                signals.append("near_52w_high")

            change_pct = q.get("change_pct", 0)
            if change_pct >= 2.0:
                signals.append("strong_day")
            elif change_pct <= -2.0:
                signals.append("weak_day")

            candidates.append(
                ScanCandidate(
                    ticker=ticker,
                    company_name=ticker,
                    sector="Unknown",
                    price=round(price, 2),
                    volume_ratio=round(vol_ratio, 2),
                    price_change_pct=round(change_pct, 2),
                    market_cap=0.0,
                    avg_volume_20d=int(avg_vol),
                    high_52w=round(high_52w, 2),
                    low_52w=round(q.get("low_52w", 0), 2),
                    scan_signals=signals,
                    initial_score=0.0,
                )
            )
        return candidates

    def _score_candidates(self, candidates: list) -> list:
        for c in candidates:
            score = 0.0
            if c.volume_ratio >= 2.0:
                score += 3.0
            elif c.volume_ratio >= 1.5:
                score += 2.0
            elif c.volume_ratio >= 1.2:
                score += 1.0

            if "near_52w_high" in c.scan_signals:
                score += 2.0
            if "volume_spike" in c.scan_signals:
                score += 2.0
            if "strong_day" in c.scan_signals:
                score += 1.5
            if c.price_change_pct > 0:
                score += min(c.price_change_pct * 0.2, 1.0)

            c.initial_score = round(score, 2)
        return candidates

    def _ask_claude_to_prioritize(
        self, candidates: list, market: MarketConditions, prev_ctx: dict = None, portfolio: dict = None
    ) -> list:
        if not candidates:
            return []

        top30 = candidates[:30]
        candidate_text = "\n".join(
            f"- {c.ticker}: price=${c.price:.2f}, vol_ratio={c.volume_ratio:.1f}x, "
            f"change={c.price_change_pct:+.1f}%, signals={','.join(c.scan_signals) or 'none'}, "
            f"score={c.initial_score}"
            for c in top30
        )

        prev_picks = ""
        if prev_ctx and prev_ctx.get("candidates"):
            prev_tickers = [c["ticker"] for c in prev_ctx["candidates"][:5]]
            prev_picks = f"\nPrevious day's top picks: {', '.join(prev_tickers)}"

        portfolio_ctx = ""
        if portfolio:
            held = portfolio.get("acciones", []) + portfolio.get("etfs", [])
            if held:
                lines = []
                for p in held:
                    gp = p.get("gp_pct", 0)
                    lines.append(
                        f"  - {p['ticker']} ({p['nombre']}): {p['cantidad']} units, "
                        f"entry={p.get('bep_usd') or p.get('bep_eur','?')}, "
                        f"current G/P={gp:+.1f}%, sector={p.get('sector','?')}"
                    )
                sectors = list({p.get("sector", "") for p in held})
                summary = portfolio.get("account_summary", {})
                portfolio_ctx = (
                    f"\nCURRENT PORTFOLIO (already held — avoid adding more exposure to same sectors):\n"
                    + "\n".join(lines)
                    + f"\nExposed sectors: {', '.join(sectors)}"
                    + f"\nFree margin available: €{summary.get('margen_libre_eur', '?')}"
                    + f"\nTotal unrealized P&L: €{summary.get('total_bp_eur', '?')}"
                )

        user_msg = f"""Market conditions: {market.spy_trend} | VIX: {market.vix_level} | {market.regime}
Crypto: BTC ${market.btc_price:,.0f} ({market.btc_change_pct:+.2f}%) | ETH ${market.eth_price:,.0f} ({market.eth_change_pct:+.2f}%)
{prev_picks}{portfolio_ctx}

Candidates to prioritize (top 30 by initial score):
{candidate_text}

Prioritize the top {TA_TOP_N} tickers most suitable for swing trades in the current market regime.
Consider the existing portfolio to avoid sector overconcentration and correlated risk."""

        try:
            result = self._call_claude_json(
                SCANNER_SYSTEM,
                user_msg,
                schema_hint='{"prioritized_tickers": ["AAPL", "NVDA", ...], "notes": "..."}',
            )
            return result.get("prioritized_tickers", [])
        except Exception as e:
            self.logger.warning(f"Claude prioritization failed: {e}. Using score-based ordering.")
            return [c.ticker for c in top30]
