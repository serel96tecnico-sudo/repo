from datetime import datetime

import anthropic

from agents.base_agent import BaseAgent
from config import (
    PORTFOLIO_VALUE, MAX_POSITION_PCT, MIN_RR_RATIO, ATR_STOP_MULTIPLIER, RISK_TOP_N,
    MIN_RISK_PER_TRADE, MAX_RISK_PER_TRADE, MODEL_PREMIUM,
)
from models.schemas import TAResult, SentimentResult, RiskResult


RISK_SYSTEM = """You are a professional risk manager for a swing trading desk.

Given a trade setup with entry, stop-loss, and targets, you will:
1. Validate that the stop-loss placement makes technical sense
2. Identify any concerns about the setup (e.g., stop too tight, target unrealistic, earnings risk)
3. Suggest adjustments if needed
4. Assign a risk score from 0-10 (10 = best risk/reward, lowest risk)

A good swing trade setup has:
- Stop loss at a logical technical level (below support, below EMA, below pattern low)
- Minimum R:R of 1.5:1
- Entry near support, not extended from moving averages
- Clear price target at resistance or measured move

Be concise and actionable."""


class RiskManager(BaseAgent):
    # Decisión crítica de gestión de riesgo → modelo premium (Opus 4.8)
    MODEL = MODEL_PREMIUM

    def __init__(self, client: anthropic.Anthropic):
        super().__init__(client)

    def run(self, ta_results: list, sentiment_map: dict, portfolio: dict = None, session: str = "morning") -> list:
        today = datetime.now().strftime("%Y-%m-%d")
        results = []
        top = ta_results[:RISK_TOP_N]
        self.logger.info(f"Running risk assessment on {len(top)} candidates...")

        # Tickers ya en cartera — evitar doblar posición
        held_tickers = set()
        if portfolio:
            for p in portfolio.get("acciones", []) + portfolio.get("etfs", []):
                held_tickers.add(p["ticker"].upper())

        for i, ta in enumerate(top):
            sent = sentiment_map.get(ta.ticker)
            if ta.ticker.upper() in held_tickers:
                self.logger.info(f"  Skipping {ta.ticker} — already held in portfolio")
                continue

            if sent and any(
                flag in sent.risk_flags for flag in ["legal_risk", "dilution_risk"]
            ):
                self.logger.info(f"  Skipping {ta.ticker} — hard risk flag: {sent.risk_flags}")
                continue

            self.logger.info(f"  Risk [{i+1}/{len(top)}]: {ta.ticker}")
            result = self._calculate_risk_params(ta, sent, session=session)

            if result and result.rr_ratio_1 >= MIN_RR_RATIO:
                results.append(result)
            else:
                rr = result.rr_ratio_1 if result else 0
                self.logger.info(f"  {ta.ticker} failed R:R filter ({rr:.2f} < {MIN_RR_RATIO})")

        results.sort(key=lambda r: r.risk_score, reverse=True)
        return results

    def _calculate_risk_params(self, ta: TAResult, sent: SentimentResult = None, session: str = "morning") -> RiskResult:
        try:
            price = ta.indicators.get("price", 0) or getattr(ta, "last_price", 0)
            atr = ta.indicators.get("atr_14") or (price * 0.02)
            ema9 = ta.indicators.get("ema9", price) or price
            ema21 = ta.indicators.get("ema21", price) or price
            support = ta.support_levels
            resistance = ta.resistance_levels

            if not price or not atr:
                self.logger.warning(f"  {ta.ticker}: price={price} atr={atr} — skipping risk calc")
                return None

            direction = getattr(ta, "direction", "long")

            # Pipeline tarde: entrada a precio de mercado para ejecución antes del cierre
            near_market = (session == "evening")

            if direction == "short":
                # CORTO: entrada cerca de resistencia, stop por encima, targets en soportes
                if near_market:
                    entry = round(price, 2)
                elif resistance:
                    entry = round(resistance[0] * 0.999, 2)
                elif price < ema9 * 1.01:
                    entry = round(ema9 * 0.999, 2)
                else:
                    entry = round(price, 2)

                entry_zone_low = round(entry * 0.995, 2)
                entry_zone_high = round(entry * 1.005, 2)

                stop_loss = round(entry + (atr * ATR_STOP_MULTIPLIER), 2)
                if resistance:
                    technical_stop = round(resistance[0] * 1.01, 2)
                    stop_loss = min(stop_loss, technical_stop) if technical_stop > entry else stop_loss

                risk_per_share = round(stop_loss - entry, 2)
                if risk_per_share <= 0:
                    return None

                min_t1 = round(entry - risk_per_share * 1.6, 2)
                min_t2 = round(entry - risk_per_share * 2.5, 2)
                raw_t1 = round(support[0], 2) if support else None
                raw_t2 = round(support[1], 2) if len(support) > 1 else None
                target_1 = min(raw_t1, min_t1) if raw_t1 else min_t1
                target_2 = min(raw_t2, min_t2) if raw_t2 else min_t2

                rr1 = round((entry - target_1) / risk_per_share, 2) if risk_per_share > 0 else 0
                rr2 = round((entry - target_2) / risk_per_share, 2) if risk_per_share > 0 else 0

            else:
                # LARGO: entrada cerca de soporte, stop por debajo, targets en resistencias
                if near_market:
                    entry = round(price, 2)
                elif support and support[0] > price * 0.97:
                    entry = round(support[0] * 1.002, 2)
                elif price > ema9 * 0.99:
                    entry = round(ema9 * 1.001, 2)
                else:
                    entry = round(price, 2)

                entry_zone_low = round(entry * 0.995, 2)
                entry_zone_high = round(entry * 1.005, 2)

                stop_loss = round(entry - (atr * ATR_STOP_MULTIPLIER), 2)
                if support:
                    technical_stop = round(support[0] * 0.99, 2)
                    stop_loss = max(stop_loss, technical_stop) if technical_stop < entry else stop_loss

                risk_per_share = round(entry - stop_loss, 2)
                if risk_per_share <= 0:
                    return None

                min_t1 = round(entry + risk_per_share * 1.6, 2)
                min_t2 = round(entry + risk_per_share * 2.5, 2)
                raw_t1 = round(resistance[0], 2) if resistance else None
                raw_t2 = round(resistance[1], 2) if len(resistance) > 1 else None
                target_1 = max(raw_t1, min_t1) if raw_t1 else min_t1
                target_2 = max(raw_t2, min_t2) if raw_t2 else min_t2

                rr1 = round((target_1 - entry) / risk_per_share, 2) if risk_per_share > 0 else 0
                rr2 = round((target_2 - entry) / risk_per_share, 2) if risk_per_share > 0 else 0

            # Número de acciones: basado en riesgo fijo, con tope de capital
            target_risk = (MIN_RISK_PER_TRADE + MAX_RISK_PER_TRADE) / 2  # $550
            shares_by_risk = max(1, int(target_risk / risk_per_share))
            shares_by_capital = max(1, int(PORTFOLIO_VALUE * MAX_POSITION_PCT / entry))
            position_size_shares = min(shares_by_risk, shares_by_capital)
            position_size_dollars = position_size_shares * entry
            position_size_pct = round(position_size_dollars / PORTFOLIO_VALUE * 100, 1)
            max_loss = round(position_size_shares * risk_per_share, 2)

            risk_score = self._ask_claude_risk_score(ta, entry, stop_loss, target_1, target_2, rr1, sent)

            return RiskResult(
                ticker=ta.ticker,
                entry_price=entry,
                entry_zone_low=entry_zone_low,
                entry_zone_high=entry_zone_high,
                stop_loss=stop_loss,
                target_1=target_1,
                target_2=target_2,
                risk_per_share=risk_per_share,
                rr_ratio_1=rr1,
                rr_ratio_2=rr2,
                position_size_pct=position_size_pct,
                position_size_shares=position_size_shares,
                max_loss_dollars=max_loss,
                holding_days_estimate="1-3 trading days" if near_market else "5-10 trading days",
                risk_score=risk_score,
            )

        except Exception as e:
            self.logger.error(f"Risk calculation failed for {ta.ticker}: {e}")
            return None

    def _ask_claude_risk_score(
        self,
        ta: TAResult,
        entry: float,
        stop: float,
        t1: float,
        t2: float,
        rr1: float,
        sent: SentimentResult = None,
    ) -> float:
        try:
            risk_flags = sent.risk_flags if sent else []
            sentiment = sent.overall_sentiment if sent else "neutral"

            user_msg = f"""Trade setup for {ta.ticker}:
Entry: ${entry:.2f} | Stop: ${stop:.2f} | Target1: ${t1:.2f} | Target2: ${t2:.2f}
R:R ratio to T1: {rr1:.2f}
Pattern: {ta.pattern_detected} | Entry trigger: {ta.entry_trigger}
ATR: {ta.indicators.get('atr_14', 'N/A')} | ADX: {ta.indicators.get('adx_14', 'N/A')}
Sentiment: {sentiment} | Risk flags: {risk_flags or 'none'}
TA summary: {ta.ta_summary}

Return JSON: {{"risk_score": 7.5, "concerns": ["string"], "validated": true}}"""

            resp = self._call_claude_json(
                RISK_SYSTEM,
                user_msg,
                schema_hint='{"risk_score": 0.0, "concerns": [], "validated": true}',
            )
            return round(float(resp.get("risk_score", 5.0)), 2)
        except Exception:
            return 5.0
