import time
from datetime import datetime
from pathlib import Path

import anthropic
import httpx

from config import (
    ANTHROPIC_API_KEY, CONTEXT_DIR, OUTPUT_DIR, LOGS_DIR,
    SCORE_WEIGHTS, FINAL_REPORT_N, US_MARKET_HOLIDAYS_2026,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    PORTFOLIO_VALUE, SUBTHEME_MAX_PCT, PORTFOLIO_RISK_CAP_PCT,
    SHORT_EXTENDED_ATR_MAX, RECENT_LOSS_COOLDOWN_DAYS, ADX_TREND_MIN,
)
from utils.risk_policy import (
    build_tier_map, classify_tier, high_beta_cap, is_high_beta, is_explicitly_classified,
    open_position_risk, recent_loss_cooldown,
)
from agents.market_scanner import MarketScanner
from agents.fundamental_analyst import FundamentalAnalyst
from agents.technical_analyst import TechnicalAnalyst
from agents.news_sentiment import NewsSentimentAnalyst
from agents.risk_manager import RiskManager
from agents.report_writer import ReportWriter
from data.market_data import MarketDataFetcher
from data.news_fetcher import NewsFetcher
from models.schemas import FinalCandidate, DailyReport
from utils.context_manager import ContextManager
from utils.logger import get_logger
from utils import telegram_notifier


class TradingOrchestrator:
    def __init__(self, override_tickers: list = None, dry_run: bool = False, session: str = "morning"):
        self.logger = get_logger("Orchestrator", LOGS_DIR)
        self.override_tickers = override_tickers
        self.dry_run = dry_run
        self.session = session  # "morning" or "evening"

        self.ctx = ContextManager(CONTEXT_DIR, OUTPUT_DIR)

        if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY.startswith("sk-ant-YOUR"):
            raise ValueError("ANTHROPIC_API_KEY not set. Edit .env file.")

        self.client = anthropic.Anthropic(
            api_key=ANTHROPIC_API_KEY,
            http_client=httpx.Client(),
        )
        self.data_fetcher = MarketDataFetcher(CONTEXT_DIR)
        self.news_fetcher = NewsFetcher()

        self.scanner = MarketScanner(self.client, self.data_fetcher)
        self.fundamental_agent = FundamentalAnalyst(self.client)
        self.ta_agent = TechnicalAnalyst(self.client, self.data_fetcher)
        self.sentiment_agent = NewsSentimentAnalyst(self.client, self.news_fetcher)
        self.risk_agent = RiskManager(self.client)
        self.report_writer = ReportWriter(self.client)

    def run_daily_pipeline(self) -> DailyReport:
        start_time = time.time()
        today = datetime.now().strftime("%Y-%m-%d")
        if self.session == "evening":
            suffix = "_evening"
        elif self.session == "webhook" and self.override_tickers:
            suffix = f"_webhook_{'_'.join(self.override_tickers)}"
        else:
            suffix = ""
        self.logger.info(f"=== Starting {self.session} pipeline for {today} ===")

        if self.session != "webhook" and not self._should_run_today():
            self.logger.info("Market closed today (weekend or holiday). Skipping.")
            return None

        prev_context = self.ctx.load_daily_state()
        portfolio = self.ctx.load_portfolio()
        if portfolio:
            held = [p["ticker"] for p in portfolio.get("acciones", []) + portfolio.get("etfs", [])]
            self.logger.info(f"Portfolio cargado: {held}")

        scan_result = None
        ta_results = []
        sentiment_results = []
        risk_results = []
        fund_map = {}

        try:
            scan_result = self._run_scan_phase(prev_context, portfolio)
        except Exception as e:
            self.logger.error(f"SCAN phase failed: {e}")
            return None

        try:
            candidates = scan_result.candidates
            if self.override_tickers:
                from models.schemas import ScanCandidate
                candidates = [
                    ScanCandidate(
                        ticker=t, company_name=t, sector="Manual",
                        price=0, volume_ratio=1.0, price_change_pct=0,
                        market_cap=0, avg_volume_20d=1_000_000,
                        high_52w=0, low_52w=0,
                    )
                    for t in self.override_tickers
                ]
            candidates, fund_map = self.fundamental_agent.run(candidates, session=self.session)
            scan_result.candidates = candidates
            self.logger.info(f"Fundamental complete: {len(fund_map)} scored")
        except Exception as e:
            self.logger.error(f"Fundamental phase failed: {e}")
            candidates = scan_result.candidates

        try:
            ta_results = self.ta_agent.run(candidates)
            self.logger.info(f"TA complete: {len(ta_results)} results")
        except Exception as e:
            self.logger.error(f"TA phase failed: {e}")

        try:
            sentiment_results = self.sentiment_agent.run(ta_results)
            self.logger.info(f"Sentiment complete: {len(sentiment_results)} results")
        except Exception as e:
            self.logger.error(f"Sentiment phase failed: {e}")

        sentiment_map = {s.ticker: s for s in sentiment_results}

        market_conditions = scan_result.market_conditions if scan_result else None
        tier_map = build_tier_map(scan_result.candidates if scan_result else [])

        try:
            risk_results = self.risk_agent.run(
                ta_results, sentiment_map, portfolio, session=self.session,
                market_conditions=market_conditions, tier_map=tier_map,
            )
            self.logger.info(f"Risk complete: {len(risk_results)} results")
        except Exception as e:
            self.logger.error(f"Risk phase failed: {e}")

        ta_map = {t.ticker: t for t in ta_results}
        final_candidates = self._merge_and_rank(risk_results, ta_map, sentiment_map, scan_result, fund_map)
        final_candidates = self._apply_trend_strength_gate(final_candidates)   # filtro C
        final_candidates = self._apply_recent_loss_cooldown(final_candidates, portfolio)
        final_candidates = self._apply_exposure_caps(
            final_candidates, tier_map, portfolio, market_conditions
        )

        if final_candidates:
            tickers = [fc.ticker for fc in final_candidates]
            fresh = self.data_fetcher.refresh_daily_quotes(tickers)
            for fc in final_candidates:
                q = fresh.get(fc.ticker)
                if q and q.get("price"):
                    fc.current_price = round(q["price"], 2)
                    if fc.scan_data:
                        fc.scan_data.price = fc.current_price
                    if fc.ta_data and "price" in fc.ta_data.indicators:
                        fc.ta_data.indicators["price"] = fc.current_price

        try:
            report = self.report_writer.run(
                final_candidates,
                scan_result.market_conditions if scan_result else None,
                start_time,
                scan_result.total_screened if scan_result else 0,
                len(ta_results),
                file_suffix=suffix,
            )
        except Exception as e:
            self.logger.error(f"Report phase failed: {e}")
            raise

        state = {
            "date": today,
            "session": self.session,
            "candidates": [fc.to_dict() for fc in final_candidates[:FINAL_REPORT_N]],
            "market": scan_result.market_conditions.to_dict() if scan_result else {},
            "total_scanned": scan_result.total_screened if scan_result else 0,
        }
        self.ctx.save_daily_state(state, file_suffix=suffix)
        self.ctx.cleanup_old_files(days_to_keep=30)

        elapsed = round(time.time() - start_time, 1)
        self.logger.info(f"=== Pipeline complete in {elapsed}s. Report: {report.report_txt_path} ===")

        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            sent = telegram_notifier.send_report(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, report)
            if sent:
                self.logger.info("Telegram notification sent.")
            else:
                self.logger.warning("Telegram notification failed (non-critical).")

        if self.session == "evening":
            try:
                from agents.portfolio_watchdog import run_watchdog
                self.logger.info("Running portfolio watchdog...")
                run_watchdog(notify=True)
            except Exception as e:
                self.logger.warning(f"Portfolio watchdog error (non-critical): {e}")

        # Arma las alertas de precio sobre los WATCH de este informe (ambas
        # sesiones). Fusiona con las ya guardadas: re-arma niveles frescos y
        # aplica gracia/caducidad. Lo consume scripts/price_watcher.py.
        try:
            from scripts.arm_alerts import run_arm
            res = run_arm(day=today, write=True, merge=True)
            self.logger.info(
                f"Alertas armadas: {len(res['alerts'])} activas, "
                f"{len(res['in_zone'])} ya en zona, {len(res['retired'])} retiradas."
            )
        except Exception as e:
            self.logger.warning(f"Armado de alertas error (non-critical): {e}")

        return report

    def _run_scan_phase(self, prev_context: dict, portfolio: dict = None):
        self.logger.info("Phase 1: Market scan")
        result = self.scanner.run(prev_context, portfolio)
        self.logger.info(f"Scan complete: {len(result.candidates)} candidates from {result.total_screened}")

        # Lo que ya está en cartera se descarta aquí y no en RiskManager (5ª fase):
        # así no gasta llamadas a Claude en fundamental + TA + sentiment para acabar
        # filtrado igualmente. De las posiciones abiertas se ocupa el watchdog, que
        # lee portfolio.json por su cuenta. RiskManager mantiene su filtro como red
        # de seguridad para las rutas que no pasan por aquí (webhook/--tickers).
        held = {
            p["ticker"].upper()
            for p in (portfolio or {}).get("acciones", []) + (portfolio or {}).get("etfs", [])
        }
        if held:
            skipped = [c.ticker for c in result.candidates if c.ticker.upper() in held]
            if skipped:
                result.candidates = [c for c in result.candidates if c.ticker.upper() not in held]
                self.logger.info(f"Descartados por estar ya en cartera: {skipped}")
        return result

    def _merge_and_rank(self, risk_results, ta_map, sentiment_map, scan_result, fund_map=None) -> list:
        scan_map = {}
        if scan_result:
            scan_map = {c.ticker: c for c in scan_result.candidates}
        if fund_map is None:
            fund_map = {}

        final = []
        for risk in risk_results:
            ticker = risk.ticker
            ta = ta_map.get(ticker)
            sent = sentiment_map.get(ticker)
            scan = scan_map.get(ticker)
            fund = fund_map.get(ticker)

            if not ta:
                continue

            ta_score_norm = ta.ta_score
            scan_score = scan.initial_score if scan else 5.0
            scan_norm = min(scan_score / 10.0 * 10.0, 10.0)
            sent_norm = sent.sentiment_score_normalized if sent else 5.0
            risk_norm = risk.risk_score
            fund_norm = fund.fundamental_score if fund else 5.0

            composite = round(
                SCORE_WEIGHTS["scan"] * scan_norm
                + SCORE_WEIGHTS.get("fundamental", 0.0) * fund_norm
                + SCORE_WEIGHTS["ta"] * ta_score_norm
                + SCORE_WEIGHTS["sentiment"] * sent_norm
                + SCORE_WEIGHTS["risk"] * risk_norm,
                2,
            )

            is_short = ta.direction == "short"

            # Regime adjustment: bonus/malus según tendencia de mercado
            regime_adj = 0.0
            # Umbral por defecto exigente para cortos: en mercado neutral/ambiguo un
            # corto necesita una señal técnica fuerte (7.5), no apenas aprobado (6.0).
            short_min_score = 7.5
            block_short = False
            if scan_result and scan_result.market_conditions:
                mc = scan_result.market_conditions
                spy = mc.spy_trend or ""
                qqq = mc.qqq_trend or ""

                # Dirección del mercado = TENDENCIA DEL PRECIO (spy_trend), no la
                # etiqueta `regime` (que es de volatilidad/VIX). Antes se hacía OR con
                # regime.startswith("BULLISH") y un VIX bajo en pleno Downtrend bloqueaba
                # los cortos tratando el mercado como alcista. La volatilidad ya la
                # gestionan R1/R3 por separado.
                bullish = spy in ("Strong Uptrend", "Uptrend")
                strong_bullish = spy == "Strong Uptrend"
                pullback = spy == "Pullback"
                bearish = spy in ("Strong Downtrend", "Downtrend")
                strong_bearish = spy == "Strong Downtrend"

                if strong_bullish:
                    regime_adj = +0.8 if not is_short else -2.0
                    short_min_score = 9.0
                    # Mercado claramente alcista: no abrir cortos salvo señal excepcional
                    block_short = is_short
                elif bullish:
                    regime_adj = +0.5 if not is_short else -1.2
                    short_min_score = 8.5
                elif pullback:
                    # Dip dentro de tendencia alcista: buscar largos en buenas acciones.
                    # Cortos casi off (umbral 8.5, sin bonus). R5 sigue exigiendo que la
                    # entrada larga espere el reclaim de fuerza (no se compra el cuchillo).
                    regime_adj = +0.4 if not is_short else -1.0
                    short_min_score = 8.5
                elif strong_bearish:
                    regime_adj = -1.0 if not is_short else +0.8
                    # Mercado claramente bajista: cortos a favor de tendencia, umbral normal
                    short_min_score = 6.5
                elif bearish:
                    regime_adj = -0.5 if not is_short else +0.5
                    short_min_score = 7.0

                # QQQ confirma: si ambos índices bullish/bearish, amplificar ligeramente
                if qqq in ("Strong Uptrend", "Uptrend") and bullish:
                    regime_adj += 0.2 if not is_short else -0.3
                elif qqq in ("Strong Downtrend", "Downtrend") and bearish:
                    regime_adj += -0.2 if not is_short else +0.2

            if regime_adj != 0.0:
                composite_raw = composite
                composite = round(min(10.0, max(0.0, composite + regime_adj)), 2)
                self.logger.info(
                    f"  {ticker}: regime_adj {regime_adj:+.1f} "
                    f"({'long' if not is_short else 'short'} en {getattr(scan_result.market_conditions, 'spy_trend', '?')}) "
                    f"→ {composite_raw:.1f} → {composite:.1f}"
                )

            demote_reason = ""
            if is_short and getattr(risk, "entry_extended", False):
                rec = "WATCH"
                demote_reason = (
                    f"R4: short sobre-extendido (rebote-entrada >{SHORT_EXTENDED_ATR_MAX} ATR sobre precio)"
                )
                self.logger.info(f"  {ticker}: SHORT demoted to WATCH — R4 guard ({demote_reason})")
            elif is_short and block_short:
                rec = "WATCH"
                demote_reason = "R4: cortos bloqueados (mercado claramente alcista)"
                self.logger.info(f"  {ticker}: SHORT demoted to WATCH — blocked ({demote_reason})")
            elif is_short and composite < short_min_score:
                rec = "WATCH"
                demote_reason = f"filtro de régimen: score {composite:.1f} < mín corto {short_min_score:.1f}"
                self.logger.info(f"  {ticker}: SHORT demoted to WATCH — regime filter ({demote_reason})")
            elif composite >= 7.5:
                rec = "STRONG SELL" if is_short else "STRONG BUY"
            elif composite >= 6.0:
                rec = "SELL" if is_short else "BUY"
            else:
                rec = "WATCH"
                demote_reason = f"score {composite:.1f} < 6.0 (umbral de compra)"

            final.append(
                FinalCandidate(
                    rank=0,
                    ticker=ticker,
                    company_name=scan.company_name if scan else ticker,
                    composite_score=composite,
                    recommendation=rec,
                    scan_data=scan,
                    ta_data=ta,
                    fundamental_data=fund,
                    sentiment_data=sent,
                    risk_data=risk,
                    demotion_reason=demote_reason,
                )
            )

        final.sort(key=lambda fc: fc.composite_score, reverse=True)
        for i, fc in enumerate(final):
            fc.rank = i + 1

        return final

    def _apply_exposure_caps(self, final, tier_map, portfolio, market_conditions) -> list:
        """R1 (cap de alta beta por régimen) + R2 (concentración por sub-tema)
        + R6 (tope de riesgo agregado de cartera).

        Recorre los longs aceptados (BUY/STRONG BUY) por ranking y degrada a WATCH
        los que romperían algún tope, contando la cartera existente como ya consumida.
        R6 cuenta para TODOS los longs (cualquier tier); R1/R2 solo para alta beta.
        Los cortos no se tocan (R4). Greedy: el mejor score se queda con el presupuesto.
        """
        if not final or market_conditions is None:
            return final

        cap = high_beta_cap(market_conditions)                  # R1
        hb_budget = cap * PORTFOLIO_VALUE                        # $ máx en alta beta
        subtheme_budget = SUBTHEME_MAX_PCT * hb_budget          # R2 base estable
        risk_cap = PORTFOLIO_RISK_CAP_PCT * PORTFOLIO_VALUE     # R6 tope riesgo agregado
        risk_used = open_position_risk(portfolio)               # R6 riesgo ya abierto

        # Exposición ya consumida por la cartera existente
        high_beta_val = 0.0
        subtheme_val = {}
        if portfolio:
            # Acciones cuentan siempre; ETFs solo si están clasificados a propósito
            # (un ETF de materias primas/amplio es diversificador, no riesgo de nombre).
            holdings = [(p, True) for p in portfolio.get("acciones", [])] + \
                       [(p, False) for p in portfolio.get("etfs", [])]
            for p, is_stock in holdings:
                tkr = p.get("ticker", "").upper()
                if not is_stock and not is_explicitly_classified(tkr):
                    continue
                qty = p.get("cantidad", 0) or 0
                px = p.get("precio_actual_usd") or p.get("bep_usd") or 0
                val = qty * px
                if val <= 0:
                    continue
                tier, sub = tier_map.get(tkr) or classify_tier(tkr)
                if is_high_beta(tier):
                    high_beta_val += val
                    if tier == "C":
                        subtheme_val[sub] = subtheme_val.get(sub, 0.0) + val

        self.logger.info(
            f"R1/R2: cap alta beta {cap:.0%} (${hb_budget:,.0f}), "
            f"sub-tema máx ${subtheme_budget:,.0f}. "
            f"Cartera ya consume ${high_beta_val:,.0f} alta beta. "
            f"R6: tope riesgo {PORTFOLIO_RISK_CAP_PCT:.0%} (${risk_cap:,.0f}), "
            f"abierto ya ${risk_used:,.0f}."
        )

        for fc in final:  # ya ordenado por score
            if fc.recommendation not in ("BUY", "STRONG BUY"):
                continue  # WATCH y cortos (SELL/STRONG SELL) intactos
            risk = fc.risk_data
            if not risk:
                continue

            # R6 — tope de riesgo agregado (todos los longs, cualquier tier)
            new_risk = risk.max_loss_dollars or 0
            if risk_used + new_risk > risk_cap:
                self._demote(fc, f"R6 riesgo agregado >{PORTFOLIO_RISK_CAP_PCT:.0%}")
                continue

            # R1/R2 — solo alta beta consume presupuesto de exposición
            tier, sub = tier_map.get(fc.ticker.upper()) or classify_tier(fc.ticker)
            if is_high_beta(tier):
                val = (risk.position_size_shares or 0) * (risk.entry_price or 0)
                if high_beta_val + val > hb_budget:
                    self._demote(fc, f"R1 cap alta beta {cap:.0%} superado")
                    continue
                if tier == "C":
                    if subtheme_val.get(sub, 0.0) + val > subtheme_budget:
                        self._demote(fc, f"R2 concentración sub-tema '{sub}' >40%")
                        continue
                    subtheme_val[sub] = subtheme_val.get(sub, 0.0) + val
                high_beta_val += val

            risk_used += new_risk  # aceptada → consume riesgo agregado (R6)

        return final

    def _apply_recent_loss_cooldown(self, final, portfolio) -> list:
        """R7 — Enfriamiento de re-entrada tras pérdida reciente.

        Degrada a WATCH cualquier BUY/STRONG BUY o SELL/STRONG SELL cuyo ticker
        cerró en pérdida (> umbral) en los últimos RECENT_LOSS_COOLDOWN_DAYS días,
        SI la dirección coincide (o si el cierre perdedor no registró dirección).
        Un stop reciente que se re-recomienda a los pocos días es el patrón que más
        pérdidas repite (análisis de selección jul-2026). No toca los WATCH.
        """
        if not final or not portfolio:
            return final

        cooldown = recent_loss_cooldown(portfolio)
        if not cooldown:
            return final

        self.logger.info(
            f"R7 enfriamiento (≤{RECENT_LOSS_COOLDOWN_DAYS}d): "
            f"{', '.join(f'{t}({d['direction'] or 'any'},{d['days_ago']}d,{d['pl']:+.0f})' for t, d in cooldown.items())}"
        )

        for fc in final:
            if fc.recommendation not in ("BUY", "STRONG BUY", "SELL", "STRONG SELL"):
                continue
            entry = cooldown.get(fc.ticker.upper())
            if not entry:
                continue
            cand_dir = "short" if fc.recommendation in ("SELL", "STRONG SELL") else "long"
            # Veta si el cierre perdedor fue en la misma dirección, o si no la trae.
            if entry["direction"] in (None, cand_dir):
                self._demote(
                    fc,
                    f"R7 enfriamiento: {fc.ticker} cerró {entry['pl']:+.0f} "
                    f"hace {entry['days_ago']}d (< {RECENT_LOSS_COOLDOWN_DAYS}d)"
                )
        return final

    def _apply_trend_strength_gate(self, final) -> list:
        """Filtro C — Suelo de ADX para breakouts.

        Un setup etiquetado como breakout necesita tendencia establecida (ADX ≥
        ADX_TREND_MIN); por debajo, las rupturas son mayormente falsas (caso ARDT
        2026-07-27: 'breakout limpio' con ADX 14). Degrada a WATCH solo los
        BUY/STRONG BUY/SELL/STRONG SELL cuyo patrón/gatillo sea de ruptura y cuyo
        ADX quede corto. NO toca pullbacks ni reversiones (ahí un ADX bajo es normal).
        """
        if not final:
            return final

        for fc in final:
            if fc.recommendation not in ("BUY", "STRONG BUY", "SELL", "STRONG SELL"):
                continue
            ta = fc.ta_data
            if not ta:
                continue
            pattern = f"{getattr(ta, 'pattern_detected', '')} {getattr(ta, 'entry_trigger', '')}".lower()
            if "breakout" not in pattern and "ruptura" not in pattern:
                continue
            adx = (ta.indicators or {}).get("adx_14") or 0
            if adx < ADX_TREND_MIN:
                self._demote(
                    fc,
                    f"filtro C: breakout con ADX {adx:.0f} < {ADX_TREND_MIN} (sin tendencia establecida)"
                )
        return final

    def _demote(self, fc, reason: str):
        fc.recommendation = "WATCH"
        # Se guarda en demotion_reason (no en summary): el ReportWriter sobrescribe
        # summary después, así que una nota puesta aquí se perdería. El ReportWriter
        # lee demotion_reason para anteponer la cabecera de veredicto.
        fc.demotion_reason = (fc.demotion_reason + "; " + reason).strip("; ") if fc.demotion_reason else reason
        self.logger.info(f"  {fc.ticker}: degradado a WATCH — {reason}")

    def _should_run_today(self) -> bool:
        today = datetime.now()
        if today.weekday() >= 5:
            return False
        date_str = today.strftime("%Y-%m-%d")
        return date_str not in US_MARKET_HOLIDAYS_2026
