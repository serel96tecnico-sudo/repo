"""
TradingView data access via tradingview-ta.
Provides TA summaries, all indicator values, and batch screener queries.

Historical OHLCV is delegated to the existing MarketDataFetcher (Alpaca/yfinance).
"""

import warnings
import requests
import urllib3

# SSL patch — same AV/proxy issue as market_data.py
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message="Unverified HTTPS")
_orig_session_req = requests.Session.request


def _no_verify_req(self, method, url, **kw):
    kw.setdefault("verify", False)
    return _orig_session_req(self, method, url, **kw)


requests.Session.request = _no_verify_req

from typing import Dict, List, Any
from utils.logger import get_logger

logger = get_logger(__name__)

_TV_AVAILABLE = False
_INTERVAL_MAP: dict = {}

try:
    from tradingview_ta import TA_Handler, Interval as _TVInterval, get_multiple_analysis  # noqa: E402

    _TV_AVAILABLE = True
    _INTERVAL_MAP = {
        "1m":  _TVInterval.INTERVAL_1_MINUTE,
        "5m":  _TVInterval.INTERVAL_5_MINUTES,
        "15m": _TVInterval.INTERVAL_15_MINUTES,
        "30m": _TVInterval.INTERVAL_30_MINUTES,
        "1h":  _TVInterval.INTERVAL_1_HOUR,
        "2h":  _TVInterval.INTERVAL_2_HOURS,
        "4h":  _TVInterval.INTERVAL_4_HOURS,
        "1D":  _TVInterval.INTERVAL_1_DAY,
        "1W":  _TVInterval.INTERVAL_1_WEEK,
        "1M":  _TVInterval.INTERVAL_1_MONTH,
    }
    logger.info("tradingview-ta loaded OK")
except ImportError:
    logger.warning("tradingview-ta not installed — run: pip install tradingview-ta")

# Indicators shown by default in quick-view
KEY_INDICATORS = [
    "close", "volume", "change",
    "RSI", "RSI[1]",
    "MACD.macd", "MACD.signal",
    "EMA20", "EMA50", "EMA200",
    "SMA20", "SMA50", "SMA200",
    "BB.upper", "BB.lower", "BB.basis",
    "ATR", "ADX", "ADX+DI", "ADX-DI",
    "Stoch.K", "Stoch.D",
    "CCI20", "Mom",
    "Aroon.Up", "Aroon.Down",
]


def _fmt(v) -> Any:
    return round(v, 4) if isinstance(v, float) else v


class TradingViewFetcher:
    def is_available(self) -> bool:
        return _TV_AVAILABLE

    def get_analysis(
        self,
        symbol: str,
        exchange: str = "NASDAQ",
        screener: str = "america",
        interval: str = "1D",
    ) -> Dict[str, Any]:
        """Full TradingView TA for one symbol: recommendation + all indicators."""
        if not _TV_AVAILABLE:
            raise RuntimeError("tradingview-ta not installed. Run: pip install tradingview-ta")

        tv_interval = _INTERVAL_MAP.get(interval, _INTERVAL_MAP["1D"])
        handler = TA_Handler(
            symbol=symbol,
            screener=screener,
            exchange=exchange,
            interval=tv_interval,
        )
        a = handler.get_analysis()

        key_vals = {k: _fmt(a.indicators[k]) for k in KEY_INDICATORS if k in a.indicators and a.indicators[k] is not None}

        return {
            "symbol": symbol,
            "exchange": exchange,
            "interval": interval,
            "recommendation": a.summary["RECOMMENDATION"],
            "buy": a.summary["BUY"],
            "sell": a.summary["SELL"],
            "neutral": a.summary["NEUTRAL"],
            "key_indicators": key_vals,
            "all_indicators": a.indicators,
            "oscillators": a.oscillators,
            "moving_averages": a.moving_averages,
        }

    def get_screener_batch(
        self,
        symbols: List[str],
        screener: str = "america",
        interval: str = "1D",
    ) -> Dict[str, Dict]:
        """
        Batch TradingView analysis for multiple tickers.
        Accepts bare tickers (e.g. 'AAPL') or full 'EXCHANGE:TICKER' format.
        """
        if not _TV_AVAILABLE:
            raise RuntimeError("tradingview-ta not installed.")

        tv_interval = _INTERVAL_MAP.get(interval, _INTERVAL_MAP["1D"])

        tv_symbols, ticker_map = [], {}
        for s in symbols:
            if ":" in s:
                tv_sym, ticker = s, s.split(":")[-1]
            else:
                tv_sym, ticker = f"NASDAQ:{s}", s
            tv_symbols.append(tv_sym)
            ticker_map[tv_sym] = ticker

        analyses = get_multiple_analysis(screener=screener, interval=tv_interval, symbols=tv_symbols)

        results = {}
        for tv_sym, a in analyses.items():
            if a is None:
                continue
            ticker = ticker_map.get(tv_sym, tv_sym.split(":")[-1])
            quick = {k: _fmt(a.indicators[k]) for k in ["close", "RSI", "EMA20", "EMA50", "ATR", "ADX", "volume"] if k in a.indicators and a.indicators[k] is not None}
            results[ticker] = {
                "recommendation": a.summary["RECOMMENDATION"],
                "buy": a.summary["BUY"],
                "sell": a.summary["SELL"],
                "neutral": a.summary["NEUTRAL"],
                **quick,
            }
        return results
