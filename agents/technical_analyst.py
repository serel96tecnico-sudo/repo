from datetime import datetime

import anthropic

from agents.base_agent import BaseAgent
from config import (
    TA_TOP_N,
    MA200_PERIOD,
    MA200_DAILY_PERIOD,
    MA200_4H_PERIOD,
    MA200_CONFLUENCE_MAX,
)
from data.indicators import (
    calculate_all_indicators,
    score_technical_setup,
    ma200_position,
    ma200_confluence_modifier,
)
from data.market_data import MarketDataFetcher
from models.schemas import ScanCandidate, TAResult


TA_SYSTEM = """You are a professional technical analyst specializing in swing trading setups — both long and short.

Given a stock's technical indicator data, you will:
1. Identify the chart pattern (bull_flag, bear_flag, cup_handle, breakout, breakdown, pullback, distribution, head_and_shoulders, consolidation, uptrend, downtrend, reversal, none)
2. Identify the trade direction: "long" or "short"
3. Assess each indicator's signal (bullish, bearish, neutral)
4. Identify the entry trigger:
   - LONG examples: "pullback_to_ema9", "breakout_above_resistance", "macd_crossover", "rsi_bounce_from_oversold"
   - SHORT examples: "breakdown_below_support", "death_cross_ema9_ema21", "rejection_at_resistance", "rsi_bearish_divergence"
5. Provide a concise technical summary (2-3 sentences)
6. Assign a technical score from 0-10:
   - 10 = strongest possible setup (long OR short)
   - Score the QUALITY of the setup, not its direction bias

IMPORTANT: Short setups are equally valid. A clean bearish breakdown with volume is a 9/10 setup.
For short setups: support_levels become your profit targets, resistance_levels become your stop zone.

Focus on:
- Trend alignment (EMA stack)
- Momentum (RSI, MACD)
- Volume confirmation
- Pattern clarity
- Risk/reward of the setup

Only recommend setups where the risk is clearly defined and the setup is actionable."""


class TechnicalAnalyst(BaseAgent):
    def __init__(self, client: anthropic.Anthropic, data_fetcher: MarketDataFetcher):
        super().__init__(client)
        self.data_fetcher = data_fetcher

    def run(self, candidates: list) -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        top = candidates[:TA_TOP_N]
        self.logger.info(f"Running TA on {len(top)} candidates...")

        for i, candidate in enumerate(top):
            self.logger.info(f"  TA [{i+1}/{len(top)}]: {candidate.ticker}")
            result = self._analyze_single(candidate, today)
            results.append(result)

        results.sort(key=lambda r: r.ta_score, reverse=True)
        return results

    def _analyze_single(self, candidate: ScanCandidate, today: str) -> TAResult:
        try:
            # 1y (no 90d): indicators.py calcula high_52w/low_52w con .tail(252)
            # (252 sesiones = 52 semanas); con 90 velas devolvía un 90-day high mal
            # etiquetado como "52w". Además el EMA200 del mismo dict necesita ≥200
            # velas para ser real. La TA corre solo sobre TA_TOP_N tickers.
            df = self.data_fetcher.fetch_ohlcv(candidate.ticker, period="1y")
            if df.empty or len(df) < 30:
                self.logger.warning(f"Insufficient data for {candidate.ticker}")
                return self._empty_result(candidate.ticker, today, price=candidate.price)

            indicators = calculate_all_indicators(df)
            python_score = score_technical_setup(indicators)

            # Media de 200 sesiones en semanal/diario/4h (tendencia mayor)
            ma200 = self._ma200_multiframe(candidate.ticker)
            indicators["ma200"] = ma200

            prompt_data = self._build_ta_prompt(candidate.ticker, indicators, python_score, ma200)
            schema = (
                '{"direction": "long|short", "pattern_detected": "string", '
                '"signals": {"rsi": "bullish|bearish|neutral", '
                '"macd": "...", "ema_stack": "...", "volume": "...", "bollinger": "..."}, '
                '"entry_trigger": "string", "ta_score": 0.0, "ta_summary": "string", '
                '"support_levels": [0.0], "resistance_levels": [0.0]}'
            )

            resp = self._call_claude_json(TA_SYSTEM, prompt_data, schema_hint=schema)

            ta_score = float(resp.get("ta_score", python_score))
            direction = resp.get("direction", "long")
            # Para cortos, el score de Python (que mide setup alcista) no aplica igual
            if direction == "short":
                final_score = round(ta_score, 2)
            else:
                final_score = round((ta_score + python_score) / 2, 2)

            # Confluencia de la MA200 multi-timeframe: empuja el score hacia la
            # tendencia mayor (a favor suma, en contra resta). Acotado a [0, 10].
            ma_mod = ma200_confluence_modifier(ma200, direction, MA200_CONFLUENCE_MAX)
            final_score = round(max(0.0, min(10.0, final_score + ma_mod)), 2)

            return TAResult(
                ticker=candidate.ticker,
                analysis_date=today,
                indicators=indicators,
                signals=resp.get("signals", {}),
                support_levels=resp.get("support_levels", indicators.get("support_levels", [])),
                resistance_levels=resp.get("resistance_levels", indicators.get("resistance_levels", [])),
                direction=direction,
                pattern_detected=resp.get("pattern_detected", "none"),
                entry_trigger=resp.get("entry_trigger", ""),
                ta_score=final_score,
                ta_summary=resp.get("ta_summary", ""),
            )

        except Exception as e:
            self.logger.error(f"TA failed for {candidate.ticker}: {e}")
            return self._empty_result(candidate.ticker, today, price=candidate.price)

    def _ma200_multiframe(self, ticker: str) -> dict:
        """SMA200 en semanal, diario y 4h. El histórico diario largo sirve para la
        MA200 diaria y, resampleado a semanal, para la MA200 semanal (una sola
        descarga). El 4h va por separado. Cada marco degrada a None si no llega a
        MA200_PERIOD barras."""
        empty = ma200_position(None, MA200_PERIOD)
        out = {"weekly": dict(empty), "daily": dict(empty), "4h": dict(empty)}

        # Diario (largo) → MA200 diaria + resample semanal
        try:
            daily = self.data_fetcher.fetch_ohlcv(ticker, period=MA200_DAILY_PERIOD, timeframe="day")
            out["daily"] = ma200_position(daily, MA200_PERIOD)
            if daily is not None and not daily.empty:
                weekly = daily.resample("W").agg({
                    "Open": "first", "High": "max", "Low": "min",
                    "Close": "last", "Volume": "sum",
                }).dropna()
                out["weekly"] = ma200_position(weekly, MA200_PERIOD)
        except Exception as e:
            self.logger.warning(f"MA200 daily/weekly failed for {ticker}: {e}")

        # 4h
        try:
            h4 = self.data_fetcher.fetch_ohlcv(ticker, period=MA200_4H_PERIOD, timeframe="4hour")
            out["4h"] = ma200_position(h4, MA200_PERIOD)
        except Exception as e:
            self.logger.warning(f"MA200 4h failed for {ticker}: {e}")

        return out

    def _fmt_ma200(self, ma200: dict) -> str:
        def line(label: str, rd: dict) -> str:
            if not rd or rd.get("ma200") is None:
                return f"{label}: N/A (datos insuficientes, {rd.get('bars', 0)} barras < {MA200_PERIOD})"
            side = "ABOVE" if rd.get("above") else "BELOW"
            slope = "rising" if rd.get("slope_up") else "falling"
            return (f"{label}: MA200={rd['ma200']} | price {side} ({rd['price_vs_ma200_pct']:+}%) "
                    f"| MA200 {slope}")
        return "\n".join([
            line("Weekly", ma200.get("weekly", {})),
            line("Daily", ma200.get("daily", {})),
            line("4H", ma200.get("4h", {})),
        ])

    def _build_ta_prompt(self, ticker: str, indicators: dict, python_score: float, ma200: dict = None) -> str:
        price = indicators.get("price", 0)
        ma200 = ma200 or indicators.get("ma200") or {}
        return f"""Analyze {ticker} for a swing trade setup.

Current Price: ${price:.2f}

=== TECHNICAL INDICATORS ===
RSI(14): {indicators.get('rsi_14', 'N/A')} (prev: {indicators.get('rsi_prev', 'N/A')})
MACD: {indicators.get('macd', 'N/A')} | Signal: {indicators.get('macd_signal', 'N/A')} | Histogram: {indicators.get('macd_histogram', 'N/A')} (prev: {indicators.get('macd_histogram_prev', 'N/A')})
EMA9: {indicators.get('ema9', 'N/A')} | EMA21: {indicators.get('ema21', 'N/A')} | EMA50: {indicators.get('ema50', 'N/A')}
SMA20: {indicators.get('sma20', 'N/A')} | SMA50: {indicators.get('sma50', 'N/A')}

=== 200-SESSION MA — MAJOR TREND (multi-timeframe) ===
{self._fmt_ma200(ma200)}
Major-trend rule: price above the 200 MA on all three frames = major uptrend (favor longs);
below on daily/weekly = counter-trend for a long. Weight this in your ta_score.
Bollinger: Upper={indicators.get('bb_upper', 'N/A')} | Mid={indicators.get('bb_middle', 'N/A')} | Lower={indicators.get('bb_lower', 'N/A')} | %B={indicators.get('bb_pct_b', 'N/A')}
ATR(14): {indicators.get('atr_14', 'N/A')}
ADX(14): {indicators.get('adx_14', 'N/A')}
Volume ratio vs 20d avg: {indicators.get('volume_ratio_20d', 'N/A')}x
Base-breakout (coil→ruptura, ATR-relativo): {'YES — coil ' + str(indicators.get('coil_ratio')) + '·ATR, rompe base @ ' + str(indicators.get('base_high')) + ' (setup de más calidad: consolidación tensa que rompe al alza en tendencia — favorécelo en el ta_score)' if indicators.get('base_breakout') else 'no'}
52w High: {indicators.get('high_52w', 'N/A')} | 52w Low: {indicators.get('low_52w', 'N/A')}
Support levels: {indicators.get('support_levels', [])}
Resistance levels: {indicators.get('resistance_levels', [])}

Python TA score (0-10): {python_score}

Provide your analysis as JSON."""

    def _empty_result(self, ticker: str, today: str, price: float = 0.0) -> TAResult:
        return TAResult(
            ticker=ticker,
            analysis_date=today,
            ta_score=0.0,
            ta_summary="Insufficient data for analysis.",
            indicators={"price": price, "atr_14": price * 0.02} if price else {},
        )
