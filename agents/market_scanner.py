import json
import os
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
- Broker 1 (EUR, limited catalog): Long only. European and US stocks.
- Broker 2 (USD, US stocks only): Long AND short positions available. NYSE/NASDAQ only.
(Live available capital for each broker is provided in the user message — respect it: do not prioritize shorts if Broker 2 has no free margin.)

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

        self._track_extended_tickers(candidates, portfolio)

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

            vol_ratio = q["volume"] / avg_vol if avg_vol > 0 else 1.0
            change_pct = q.get("change_pct", 0)
            # Override: massive volume spike + strong price move bypasses liquidity filter
            breakout_override = vol_ratio >= 10.0 and abs(change_pct) >= 10.0
            if avg_vol < MIN_AVG_VOLUME and not breakout_override:
                continue

            signals = []
            if breakout_override and avg_vol < MIN_AVG_VOLUME:
                signals.append("liquidity_breakout")
            if vol_ratio >= VOLUME_SPIKE_THRESHOLD:
                signals.append("volume_spike")

            high_52w = q.get("high_52w", price)
            if high_52w > 0 and price >= high_52w * NEAR_52W_HIGH_PCT:
                signals.append("near_52w_high")

            low_52w = q.get("low_52w", price)
            # cerca de mínimos de 52 semanas (dentro del 15% del mínimo) → candidato short / debilidad
            if low_52w > 0 and price <= low_52w * (1 + (1 - NEAR_52W_HIGH_PCT)):
                signals.append("near_52w_low")

            if change_pct >= 5.0:
                signals.append("extended_intraday")  # ya subió mucho hoy (esperar pullback)
            elif change_pct >= 2.0:
                signals.append("strong_day")
            elif change_pct <= -5.0:
                signals.append("breakdown_day")      # caída fuerte → posible setup short
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
            sig = c.scan_signals

            # Volumen: relevante para ambas direcciones (confirma el movimiento)
            vol_score = 0.0
            if c.volume_ratio >= 2.0:
                vol_score = 3.0
            elif c.volume_ratio >= 1.5:
                vol_score = 2.0
            elif c.volume_ratio >= 1.2:
                vol_score = 1.0

            # ── Puntuación LONG ──────────────────────────────────
            long_score = vol_score
            if "near_52w_high" in sig:
                long_score += 2.0
            if "volume_spike" in sig:
                long_score += 1.0
            if "extended_intraday" in sig:
                long_score -= 2.0  # ya subió demasiado hoy — esperar pullback
            elif "strong_day" in sig:
                long_score += 1.0 + min(c.price_change_pct * 0.2, 0.5)
            elif c.price_change_pct > 0:
                long_score += min(c.price_change_pct * 0.2, 1.0)

            # ── Puntuación SHORT ─────────────────────────────────
            short_score = vol_score
            if "near_52w_low" in sig:
                short_score += 2.0
            if "volume_spike" in sig:
                short_score += 1.0  # volumen de distribución
            if "breakdown_day" in sig:
                short_score += 1.5 + min(abs(c.price_change_pct) * 0.2, 0.5)
            elif "weak_day" in sig:
                short_score += 1.0 + min(abs(c.price_change_pct) * 0.2, 0.5)

            # La dirección del candidato es la del mejor score; initial_score = max
            if short_score > long_score:
                c.setup_direction = "short"
                c.initial_score = round(short_score, 2)
            else:
                c.setup_direction = "long"
                c.initial_score = round(long_score, 2)
        return candidates

    def _ask_claude_to_prioritize(
        self, candidates: list, market: MarketConditions, prev_ctx: dict = None, portfolio: dict = None
    ) -> list:
        if not candidates:
            return []

        top30 = candidates[:30]

        def _candidate_line(c) -> str:
            dist_high = (c.high_52w - c.price) / c.high_52w * 100 if c.high_52w > 0 else 0
            dist_low = (c.price - c.low_52w) / c.low_52w * 100 if c.low_52w > 0 else 0
            return (
                f"- {c.ticker} [{c.setup_direction.upper()}]: price=${c.price:.2f}, "
                f"vol_ratio={c.volume_ratio:.1f}x, change={c.price_change_pct:+.1f}%, "
                f"-{dist_high:.0f}% from 52w high / +{dist_low:.0f}% above 52w low, "
                f"signals={','.join(c.scan_signals) or 'none'}, score={c.initial_score}"
            )

        candidate_text = "\n".join(_candidate_line(c) for c in top30)

        prev_picks = ""
        if prev_ctx and prev_ctx.get("candidates"):
            prev_tickers = [c["ticker"] for c in prev_ctx["candidates"][:5]]
            prev_picks = f"\nPrevious day's top picks: {', '.join(prev_tickers)}"

        portfolio_ctx = ""
        broker_ctx = ""
        if portfolio:
            brokers = portfolio.get("brokers", {})
            b1 = brokers.get("broker_1", {})
            b2 = brokers.get("broker_2", {})
            b1_free = b1.get("margen_libre_eur")
            b2_free = b2.get("margin_available_usd")
            if b1_free is not None or b2_free is not None:
                b2_shorts = "shorts OK" if (b2_free or 0) > 0 else "NO free margin — shorts blocked"
                broker_ctx = (
                    f"\nLIVE BROKER CAPITAL:\n"
                    f"  - Broker 1 (EUR, long-only): €{b1_free if b1_free is not None else '?'} free margin\n"
                    f"  - Broker 2 (USD, long+short): ${b2_free if b2_free is not None else '?'} free margin ({b2_shorts})"
                )

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
{broker_ctx}{prev_picks}{portfolio_ctx}

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

    def _track_extended_tickers(self, candidates: list, portfolio: dict = None) -> None:
        """Añade tickers con extended_intraday a seguimiento en portfolio.json para entrada en pullback."""
        extended = [c for c in candidates if "extended_intraday" in c.scan_signals]
        if not extended:
            return

        portfolio_path = os.path.join("contex", "portfolio.json")
        if not os.path.exists(portfolio_path):
            return

        try:
            with open(portfolio_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.logger.warning(f"Could not load portfolio for seguimiento update: {e}")
            return

        today = datetime.now().strftime("%Y-%m-%d")
        seguimiento = data.get("seguimiento", [])

        # Tickers ya en cartera o ya en seguimiento — no duplicar
        held = {p["ticker"].upper() for p in data.get("acciones", []) + data.get("etfs", [])}
        already_tracked = {e["ticker"].upper() for e in seguimiento}

        added = []
        for c in extended:
            t = c.ticker.upper()
            if t in held or t in already_tracked:
                continue
            entry = {
                "ticker": t,
                "direccion": "long",  # extended_intraday es siempre subida fuerte
                "broker": "broker_1_o_2",
                "razon": f"Extendido hoy +{c.price_change_pct:.1f}% con volumen {c.volume_ratio:.1f}x — esperar pullback para entrada",
                "condiciones_entrada": "Pullback a EMA9/EMA21 con vela alcista de confirmación",
                "precio_extended": c.price,
                "fecha_añadido": today,
            }
            seguimiento.append(entry)
            added.append(t)

        if not added:
            return

        data["seguimiento"] = seguimiento

        tmp_path = portfolio_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, portfolio_path)
            self.logger.info(f"Seguimiento: added {len(added)} extended tickers: {added}")
        except Exception as e:
            self.logger.warning(f"Failed to write seguimiento update: {e}")
