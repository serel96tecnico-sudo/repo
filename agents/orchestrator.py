import time
from datetime import datetime
from pathlib import Path

import anthropic
import httpx

from config import (
    ANTHROPIC_API_KEY, CONTEXT_DIR, OUTPUT_DIR, LOGS_DIR,
    SCORE_WEIGHTS, FINAL_REPORT_N, US_MARKET_HOLIDAYS_2026,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
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
            http_client=httpx.Client(verify=False),
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

        try:
            risk_results = self.risk_agent.run(ta_results, sentiment_map, portfolio, session=self.session)
            self.logger.info(f"Risk complete: {len(risk_results)} results")
        except Exception as e:
            self.logger.error(f"Risk phase failed: {e}")

        ta_map = {t.ticker: t for t in ta_results}
        final_candidates = self._merge_and_rank(risk_results, ta_map, sentiment_map, scan_result, fund_map)

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
        return report

    def _run_scan_phase(self, prev_context: dict, portfolio: dict = None):
        self.logger.info("Phase 1: Market scan")
        result = self.scanner.run(prev_context, portfolio)
        self.logger.info(f"Scan complete: {len(result.candidates)} candidates from {result.total_screened}")
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
            if composite >= 7.5:
                rec = "STRONG SELL" if is_short else "STRONG BUY"
            elif composite >= 6.0:
                rec = "SELL" if is_short else "BUY"
            else:
                rec = "WATCH"

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
                )
            )

        final.sort(key=lambda fc: fc.composite_score, reverse=True)
        for i, fc in enumerate(final):
            fc.rank = i + 1

        return final

    def _should_run_today(self) -> bool:
        today = datetime.now()
        if today.weekday() >= 5:
            return False
        date_str = today.strftime("%Y-%m-%d")
        return date_str not in US_MARKET_HOLIDAYS_2026
