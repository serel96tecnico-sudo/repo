import re
import time
import requests
import urllib3
from datetime import datetime, timedelta

from agents.base_agent import BaseAgent
from models.schemas import ScanCandidate, FundamentalResult
from config import EARNINGS_BLOCK_DAYS, FUNDAMENTAL_TOP_N

urllib3.disable_warnings()

# SSL patch for finviz (same proxy workaround as Alpaca)
_orig_req = requests.Session.request
def _no_ssl(self, method, url, **kwargs):
    kwargs.setdefault("verify", False)
    return _orig_req(self, method, url, **kwargs)
requests.Session.request = _no_ssl

# Screener filter keys that finvizfinance accepts
LONG_SCREENER_FILTERS = {
    "Average Volume": "Over 500K",
    "Country": "USA",
    "Analyst Recom.": "Strong Buy (1)",
    "InsiderTransactions": "Positive (>0%)",
    "Price": "Over $5",
}
SHORT_SCREENER_FILTERS = {
    "Average Volume": "Over 500K",
    "Country": "USA",
    "Float Short": "Over 10%",
    "Performance (Week)": "Down",
    "Price": "Over $5",
}


class FundamentalAnalyst(BaseAgent):
    def __init__(self, client):
        super().__init__(client)

    def run(self, scan_candidates: list, session: str = "morning") -> tuple:
        """
        First filter + discovery phase.
        Returns (enriched_candidates, fund_map: dict[ticker, FundamentalResult])
        """
        top_n = min(len(scan_candidates), FUNDAMENTAL_TOP_N)
        candidates = scan_candidates[:top_n]
        rest = scan_candidates[top_n:]

        self.logger.info(f"FundamentalAnalyst: analyzing {len(candidates)} candidates")

        fund_map = {}
        filtered = []

        for cand in candidates:
            try:
                data = self._fetch_finviz(cand.ticker)
                if not data:
                    filtered.append(cand)
                    continue

                result = self._build_result(cand.ticker, data, cand.price)

                if result.blocked:
                    self.logger.info(f"  BLOCKED {cand.ticker}: {result.block_reason}")
                    continue

                # Enrich company name / sector from Finviz (more accurate than yfinance)
                if data.get("Company") and data["Company"] not in ("-", ""):
                    cand.company_name = data["Company"]
                if data.get("Sector") and data["Sector"] not in ("-", ""):
                    cand.sector = data["Sector"]

                fund_map[cand.ticker] = result
                filtered.append(cand)
                time.sleep(0.35)

            except Exception as e:
                self.logger.warning(f"  {cand.ticker}: Finviz fetch error — {e}")
                filtered.append(cand)

        blocked_count = len(candidates) - len(filtered)

        # Discover new candidates via Finviz screener
        existing = {c.ticker for c in filtered + rest}
        new_candidates = self._run_screener(existing, fund_map)

        all_candidates = filtered + new_candidates + rest
        self.logger.info(
            f"Fundamental complete: {len(filtered)} kept, {blocked_count} blocked, "
            f"{len(new_candidates)} new from screener → {len(all_candidates)} total"
        )
        return all_candidates, fund_map

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _fetch_finviz(self, ticker: str) -> dict:
        from finvizfinance.quote import finvizfinance
        return finvizfinance(ticker).ticker_fundament()

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _build_result(self, ticker: str, data: dict, price: float) -> FundamentalResult:
        earnings_str = data.get("Earnings", "-") or "-"
        days_to_earn = self._parse_earnings_days(earnings_str)
        short_float  = self._pf(data.get("Short Float"))
        short_ratio  = self._pf(data.get("Short Ratio"))
        target_price = self._pf(data.get("Target Price"))
        recom        = self._pf(data.get("Recom")) or 3.0
        insider_trans = self._pf(data.get("Insider Trans"))
        inst_trans   = self._pf(data.get("Inst Trans"))
        forward_pe   = self._pf(data.get("Forward P/E"))
        debt_eq      = self._pf(data.get("Debt/Eq"))
        profit_margin = self._pf(data.get("Profit Margin"))
        beta         = self._pf(data.get("Beta")) or 1.0

        risk_flags = []
        blocked = False
        block_reason = ""

        # --- Block conditions ---
        if 0 < days_to_earn <= EARNINGS_BLOCK_DAYS:
            blocked = True
            block_reason = f"Earnings in {days_to_earn}d ({earnings_str})"
        elif 0 < days_to_earn <= 5:
            risk_flags.append(f"earnings_imminent ({earnings_str})")

        # --- Fundamental score (0-10) ---
        score = 5.0

        # Analyst consensus: recom 1=Strong Buy → +1.5, 5=Strong Sell → -1.5
        score += (3.0 - recom) * 0.75

        # Analyst target vs price
        if price > 0 and target_price > 0:
            upside = (target_price - price) / price * 100
            if upside > 30:
                score += 1.0
            elif upside > 15:
                score += 0.5
            elif upside < -10:
                score -= 1.0
                risk_flags.append("price_above_analyst_target")

        # Insider activity
        if insider_trans > 10:
            score += 1.0
        elif insider_trans > 0:
            score += 0.3
        elif insider_trans < -50:
            score -= 1.5
            risk_flags.append("heavy_insider_selling")
        elif insider_trans < -20:
            score -= 0.8
            risk_flags.append("insider_selling")

        # Institutional flow
        if inst_trans > 5:
            score += 0.5
        elif inst_trans < -5:
            score -= 0.3

        # Short float (headwind for longs)
        if short_float > 25:
            risk_flags.append(f"very_high_short_float ({short_float:.0f}%)")
            score -= 0.5
        elif short_float > 15:
            risk_flags.append(f"high_short_float ({short_float:.0f}%)")

        # Leverage
        if debt_eq > 3:
            risk_flags.append("high_leverage")
            score -= 0.5

        # Earnings proximity penalty (5-10 days away)
        if 5 < days_to_earn <= 10:
            score -= 0.5

        score = round(max(0.0, min(10.0, score)), 2)

        return FundamentalResult(
            ticker=ticker,
            company_name=data.get("Company", ticker),
            sector=data.get("Sector", ""),
            earnings_date=earnings_str,
            earnings_days_away=days_to_earn,
            earnings_risk=0 < days_to_earn <= 5,
            short_float_pct=short_float,
            short_ratio=short_ratio,
            target_price=target_price,
            analyst_recom=recom,
            insider_trans_pct=insider_trans,
            inst_trans_pct=inst_trans,
            forward_pe=forward_pe,
            debt_equity=debt_eq,
            profit_margin=profit_margin,
            beta=beta,
            fundamental_score=score,
            risk_flags=risk_flags,
            blocked=blocked,
            block_reason=block_reason,
        )

    # ------------------------------------------------------------------
    # Screener — discover new candidates
    # ------------------------------------------------------------------

    def _run_screener(self, existing_tickers: set, fund_map: dict) -> list:
        from finvizfinance.screener.overview import Overview

        new_candidates = []
        configs = [
            (LONG_SCREENER_FILTERS, 8, "long_screener"),
            (SHORT_SCREENER_FILTERS, 5, "short_screener"),
        ]

        for filters, limit, label in configs:
            try:
                overview = Overview()
                overview.set_filter(filters_dict=filters)
                df = overview.screener_view(limit=limit)
                if df is None or df.empty:
                    continue

                for _, row in df.iterrows():
                    ticker = str(row.get("Ticker", "")).strip()
                    if not ticker or ticker in existing_tickers:
                        continue
                    try:
                        fdata = self._fetch_finviz(ticker)
                        price = self._pf(fdata.get("Price"))
                        if price <= 0:
                            continue
                        result = self._build_result(ticker, fdata, price)
                        if result.blocked:
                            continue
                        fund_map[ticker] = result
                        existing_tickers.add(ticker)
                        new_candidates.append(ScanCandidate(
                            ticker=ticker,
                            company_name=fdata.get("Company", ticker),
                            sector=fdata.get("Sector", "Unknown"),
                            price=price,
                            volume_ratio=1.0,
                            price_change_pct=self._pf(fdata.get("Change")),
                            market_cap=0.0,
                            avg_volume_20d=500_000,
                            high_52w=price,
                            low_52w=price,
                            scan_signals=[label],
                            initial_score=result.fundamental_score * 0.5,
                        ))
                        self.logger.info(f"  Screener new: {ticker} (score {result.fundamental_score})")
                        time.sleep(0.35)
                    except Exception:
                        continue

            except Exception as e:
                self.logger.warning(f"Screener {label} failed: {e}")

        return new_candidates

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pf(self, val) -> float:
        if not val or str(val).strip() in ("-", "", "nan"):
            return 0.0
        try:
            return float(str(val).replace("%", "").replace(",", "").strip())
        except Exception:
            return 0.0

    def _parse_earnings_days(self, earnings_str: str) -> int:
        if not earnings_str or earnings_str.strip() in ("-", ""):
            return 999
        try:
            clean = re.sub(r"\s+(AMC|BMO|--)\s*$", "", earnings_str.strip())
            year = datetime.now().year
            dt = datetime.strptime(f"{clean} {year}", "%b %d %Y")
            if dt < datetime.now() - timedelta(days=1):
                dt = dt.replace(year=year + 1)
            return max(0, (dt.date() - datetime.now().date()).days)
        except Exception:
            return 999
