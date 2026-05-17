import json
import os
from datetime import datetime
from pathlib import Path

import anthropic

from agents.base_agent import BaseAgent
from config import OUTPUT_DIR, FINAL_REPORT_N, SCORE_WEIGHTS
from models.schemas import FinalCandidate, DailyReport, MarketConditions, TAResult, SentimentResult, RiskResult


REPORT_SYSTEM = """Eres un analista profesional de swing trading que redacta un informe diario de operaciones.

El informe debe ser claro, accionable y profesional. Para cada candidato proporciona:
- Una narrativa concisa explicando POR QUÉ esta acción es una oportunidad de swing trade ahora mismo
- Qué catalizador o evento técnico ha activado el setup
- El riesgo clave a vigilar

Escribe con un estilo directo y seguro. Evita el lenguaje ambiguo. Sé específico con los niveles de precio.
El público es un trader activo que quiere setups claros y accionables.

IMPORTANTE: Responde SIEMPRE en español."""


class ReportWriter(BaseAgent):
    def __init__(self, client: anthropic.Anthropic):
        super().__init__(client)

    def run(
        self,
        final_candidates: list,
        market: MarketConditions,
        start_time: float,
        total_scanned: int,
        total_analyzed: int,
        file_suffix: str = "",
    ) -> DailyReport:
        today = datetime.now().strftime("%Y-%m-%d")
        elapsed = round(datetime.now().timestamp() - start_time, 1)

        top = final_candidates[:FINAL_REPORT_N]
        summaries = self._generate_summaries(top, market)

        for i, fc in enumerate(top):
            fc.rank = i + 1
            if i < len(summaries):
                fc.summary = summaries[i]

        report_text = self._format_report_text(top, market, today, elapsed, total_scanned, total_analyzed, file_suffix)

        txt_path = OUTPUT_DIR / f"report_{today}{file_suffix}.txt"
        json_path = OUTPUT_DIR / f"report_{today}{file_suffix}.json"

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        txt_path.write_text(report_text, encoding="utf-8")

        report = DailyReport(
            report_date=today,
            market_conditions=market,
            candidates=top,
            report_text=report_text,
            report_json_path=str(json_path),
            report_txt_path=str(txt_path),
            generation_time_seconds=elapsed,
            total_scanned=total_scanned,
            total_analyzed=total_analyzed,
        )

        tmp = json_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8"
        )
        os.replace(tmp, json_path)

        self.logger.info(f"Report saved: {txt_path}")
        return report

    def _generate_summaries(self, candidates: list, market: MarketConditions) -> list:
        if not candidates:
            return []

        candidates_text = ""
        for i, fc in enumerate(candidates, 1):
            ta = fc.ta_data
            sent = fc.sentiment_data
            risk = fc.risk_data
            fund = fc.fundamental_data
            current_px = f"${fc.current_price:.2f}" if fc.current_price else "N/A"
            fund_line = ""
            if fund:
                fund_line = (
                    f"Fundamental score: {fund.fundamental_score:.1f} | "
                    f"Analyst recom: {fund.analyst_recom:.1f} | "
                    f"Target: ${fund.target_price:.2f} | "
                    f"Short float: {fund.short_float_pct:.1f}% | "
                    f"Insider trans: {fund.insider_trans_pct:+.1f}%"
                )
                if fund.risk_flags:
                    fund_line += f" | Flags: {', '.join(fund.risk_flags)}"
            candidates_text += f"""
--- SETUP {i}: {fc.ticker} ({fc.company_name}) | Score: {fc.composite_score:.1f} | {fc.recommendation} ---
Current Price: {current_px} | Pattern: {ta.pattern_detected if ta else 'N/A'} | Entry trigger: {ta.entry_trigger if ta else 'N/A'}
Entry: ${risk.entry_price:.2f} | Stop: ${risk.stop_loss:.2f} | T1: ${risk.target_1:.2f} | T2: ${risk.target_2:.2f}
R:R: {risk.rr_ratio_1:.1f}:1 | Hold: {risk.holding_days_estimate}
Sentiment: {sent.overall_sentiment if sent else 'N/A'} | Catalyst: {sent.catalyst_description if sent and sent.catalyst_found else 'None'}
TA summary: {ta.ta_summary if ta else 'N/A'}
{fund_line}
"""

        user_msg = f"""Mercado: {market.spy_trend} | VIX: {market.vix_level} | {market.regime}
BTC: ${market.btc_price:,.0f} ({market.btc_change_pct:+.2f}%) | ETH: ${market.eth_price:,.0f} ({market.eth_change_pct:+.2f}%)

Escribe una narrativa de 2-3 frases en ESPAÑOL para cada uno de estos {len(candidates)} setups de swing trade.
Explica: (1) por qué esta acción está en setup ahora, (2) qué vigilar para la entrada, (3) riesgo clave.

{candidates_text}

FORMATO DE RESPUESTA: Escribe exactamente {len(candidates)} bloques separados por la línea "===". Sin numeración, sin encabezados, solo el texto de la narrativa seguido de "===". Ejemplo:
Narrativa del setup 1 en una sola línea continua sin saltos de línea.
===
Narrativa del setup 2 en una sola línea continua sin saltos de línea.
==="""

        try:
            raw = self._call_claude(REPORT_SYSTEM, user_msg, max_tokens=2048)
            parts = [p.strip() for p in raw.split("===") if p.strip()]
            if len(parts) >= len(candidates):
                return parts[:len(candidates)]
            # fallback: try to get at least something
            if parts:
                while len(parts) < len(candidates):
                    parts.append("")
                return parts
            return ["" for _ in candidates]
        except Exception as e:
            self.logger.error(f"Summary generation failed: {e}")
            return ["" for _ in candidates]

    def _format_report_text(
        self,
        candidates: list,
        market: MarketConditions,
        today: str,
        elapsed: float,
        total_scanned: int,
        total_analyzed: int,
        file_suffix: str = "",
    ) -> str:
        now = datetime.now().strftime("%H:%M:%S")
        session_label = " (EVENING — Next-Day Setups)" if file_suffix == "_evening" else ""
        lines = [
            "=" * 60,
            f"SWING TRADING REPORT{session_label} — {today}",
            f"Generated: {now} EST  |  Runtime: {elapsed}s",
            f"Scanned: {total_scanned} tickers  |  Analyzed: {total_analyzed}",
            "=" * 60,
            "",
            "MARKET CONDITIONS",
            "-" * 40,
            f"SPY Trend:  {market.spy_trend}",
            f"QQQ Trend:  {market.qqq_trend}",
            f"VIX:        {market.vix_level}",
            f"Regime:     {market.regime}",
            f"BTC:        ${market.btc_price:,.0f} ({market.btc_change_pct:+.2f}%)",
            f"ETH:        ${market.eth_price:,.0f} ({market.eth_change_pct:+.2f}%)",
            "",
            "=" * 60,
            f"TOP {len(candidates)} SWING TRADE CANDIDATES",
            "=" * 60,
        ]

        for fc in candidates:
            ta = fc.ta_data
            sent = fc.sentiment_data
            risk = fc.risk_data
            fund = fc.fundamental_data

            direction = ta.direction if ta else "long"
            direction_label = "LARGO (LONG)" if direction == "long" else "CORTO (SHORT)"
            broker_label = "Broker 1 o Broker 2" if direction == "long" else "Broker 2 ÚNICAMENTE"

            price_display = fc.current_price if fc.current_price else (risk.entry_price if risk else 0)
            entry_price = risk.entry_price if risk else price_display
            price_vs_entry = f"  ({((price_display - entry_price) / entry_price * 100):+.1f}% vs entry)" if risk and price_display and price_display != entry_price else ""
            lines += [
                "",
                f"#{fc.rank}  {fc.ticker} — {fc.company_name}  [{fc.recommendation} | Score: {fc.composite_score:.1f}]",
                f"    Dirección: {direction_label}  |  Ejecutar en: {broker_label}",
                "-" * 60,
                f"Current Price: ${price_display:.2f}{price_vs_entry}",
            ]

            if risk:
                lines += [
                    "",
                    f"ENTRY ZONE:  ${risk.entry_zone_low:.2f}–${risk.entry_zone_high:.2f}",
                    f"STOP LOSS:   ${risk.stop_loss:.2f}  ({((risk.stop_loss - risk.entry_price) / risk.entry_price * 100):+.1f}%)",
                    f"TARGET 1:    ${risk.target_1:.2f}  ({((risk.target_1 - risk.entry_price) / risk.entry_price * 100):+.1f}%)  R:R = 1:{risk.rr_ratio_1:.1f}",
                    f"TARGET 2:    ${risk.target_2:.2f}  ({((risk.target_2 - risk.entry_price) / risk.entry_price * 100):+.1f}%)  R:R = 1:{risk.rr_ratio_2:.1f}",
                    f"HOLD:        {risk.holding_days_estimate}",
                    f"POSITION:    {risk.position_size_pct}% of portfolio ({risk.position_size_shares} shares | max loss ${risk.max_loss_dollars:.2f})",
                ]

            if ta:
                ind = ta.indicators
                lines += [
                    "",
                    "TECHNICAL SETUP:",
                    f"  Pattern:     {ta.pattern_detected}",
                    f"  Entry type:  {ta.entry_trigger}",
                    f"  RSI(14):     {ind.get('rsi_14', 'N/A')}",
                    f"  MACD hist:   {ind.get('macd_histogram', 'N/A')} (prev: {ind.get('macd_histogram_prev', 'N/A')})",
                    f"  EMA stack:   price={ind.get('price','?')} | 9={ind.get('ema9','?')} | 21={ind.get('ema21','?')} | 50={ind.get('ema50','?')}",
                    f"  Volume:      {ind.get('volume_ratio_20d', 'N/A')}x 20d avg",
                    f"  ADX(14):     {ind.get('adx_14', 'N/A')}",
                    f"  Support:     {ta.support_levels}",
                    f"  Resistance:  {ta.resistance_levels}",
                ]

            if fund:
                upside = ""
                if fund.target_price > 0 and fc.current_price > 0:
                    upside_pct = (fund.target_price - fc.current_price) / fc.current_price * 100
                    upside = f"  ({upside_pct:+.0f}% upside)"
                earn_line = f"{fund.earnings_date} ({fund.earnings_days_away}d)" if fund.earnings_date != "-" else "N/A"
                flags_str = f"  ⚠ {', '.join(fund.risk_flags)}" if fund.risk_flags else ""
                lines += [
                    "",
                    "FUNDAMENTAL:",
                    f"  Score:       {fund.fundamental_score:.1f}/10",
                    f"  Analyst rec: {fund.analyst_recom:.1f} (1=Strong Buy, 5=Strong Sell)  Target: ${fund.target_price:.2f}{upside}",
                    f"  Short float: {fund.short_float_pct:.1f}%  Short ratio: {fund.short_ratio:.1f}",
                    f"  Insider:     {fund.insider_trans_pct:+.1f}%  Institutional: {fund.inst_trans_pct:+.1f}%",
                    f"  Earnings:    {earn_line}{flags_str}",
                ]

            if sent:
                flags = f" ⚠ {', '.join(sent.risk_flags)}" if sent.risk_flags else ""
                catalyst = f"\n  Catalyst:    {sent.catalyst_description}" if sent.catalyst_found else ""
                lines += [
                    "",
                    f"SENTIMENT: {sent.overall_sentiment.upper()} ({sent.sentiment_score:+.2f}){flags}{catalyst}",
                ]

            if fc.summary:
                lines += ["", f"ANALYSIS: {fc.summary}"]

            lines.append("-" * 60)

        lines += [
            "",
            "=" * 60,
            "DISCLAIMER",
            "=" * 60,
            "This report is generated by an AI system for informational",
            "purposes only. Not financial advice. Always do your own",
            "due diligence before trading.",
            "=" * 60,
        ]

        return "\n".join(lines)
