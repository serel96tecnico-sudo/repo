from datetime import datetime

import anthropic

from agents.base_agent import BaseAgent
from config import TA_TOP_N
from data.indicators import calculate_all_indicators, score_technical_setup
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
            df = self.data_fetcher.fetch_ohlcv(candidate.ticker, period="90d")
            if df.empty or len(df) < 30:
                self.logger.warning(f"Insufficient data for {candidate.ticker}")
                return self._empty_result(candidate.ticker, today, price=candidate.price)

            indicators = calculate_all_indicators(df)
            python_score = score_technical_setup(indicators)

            prompt_data = self._build_ta_prompt(candidate.ticker, indicators, python_score)
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

    def _build_ta_prompt(self, ticker: str, indicators: dict, python_score: float) -> str:
        price = indicators.get("price", 0)
        return f"""Analyze {ticker} for a swing trade setup.

Current Price: ${price:.2f}

=== TECHNICAL INDICATORS ===
RSI(14): {indicators.get('rsi_14', 'N/A')} (prev: {indicators.get('rsi_prev', 'N/A')})
MACD: {indicators.get('macd', 'N/A')} | Signal: {indicators.get('macd_signal', 'N/A')} | Histogram: {indicators.get('macd_histogram', 'N/A')} (prev: {indicators.get('macd_histogram_prev', 'N/A')})
EMA9: {indicators.get('ema9', 'N/A')} | EMA21: {indicators.get('ema21', 'N/A')} | EMA50: {indicators.get('ema50', 'N/A')} | EMA200: {indicators.get('ema200', 'N/A')}
SMA20: {indicators.get('sma20', 'N/A')} | SMA50: {indicators.get('sma50', 'N/A')}
Bollinger: Upper={indicators.get('bb_upper', 'N/A')} | Mid={indicators.get('bb_middle', 'N/A')} | Lower={indicators.get('bb_lower', 'N/A')} | %B={indicators.get('bb_pct_b', 'N/A')}
ATR(14): {indicators.get('atr_14', 'N/A')}
ADX(14): {indicators.get('adx_14', 'N/A')}
Volume ratio vs 20d avg: {indicators.get('volume_ratio_20d', 'N/A')}x
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
