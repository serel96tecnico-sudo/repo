import os
import time
import warnings
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Fix SSL for environments with proxy/AV certificate interception (e.g. Avast)
# curl_cffi respects CURL_CA_BUNDLE; set it to certifi's bundle before any import
try:
    import certifi as _certifi
    os.environ.setdefault('CURL_CA_BUNDLE', _certifi.where())
    os.environ.setdefault('REQUESTS_CA_BUNDLE', _certifi.where())
except Exception:
    pass

# yfinance 1.3+ uses curl_cffi — patch SSL verify for corporate/proxy environments
try:
    import curl_cffi.requests as _ccffi
    _orig_req = _ccffi.Session.request
    def _ssl_patched(self, method, url, **kw):
        kw.setdefault('verify', False)
        return _orig_req(self, method, url, **kw)
    _ccffi.Session.request = _ssl_patched
    warnings.filterwarnings('ignore', message='Unverified HTTPS')
except Exception:
    pass

import yfinance as yf

from models.schemas import MarketConditions
from utils.logger import get_logger
from config import ALPACA_API_KEY, ALPACA_API_SECRET

logger = get_logger(__name__)

FALLBACK_SP500 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "BRK-B",
    "AVGO", "JPM", "LLY", "V", "UNH", "XOM", "MA", "JNJ", "PG", "COST", "HD",
    "MRK", "ABBV", "CVX", "CRM", "BAC", "NFLX", "AMD", "KO", "PEP", "TMO",
    "ACN", "LIN", "MCD", "ADBE", "CSCO", "ABT", "WMT", "TXN", "DHR", "PM",
    "ORCL", "CAT", "INTU", "AMGN", "NEE", "IBM", "QCOM", "RTX", "GS", "ISRG",
]

FALLBACK_NDX100 = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "TSLA", "AVGO",
    "COST", "NFLX", "AMD", "ADBE", "QCOM", "CSCO", "INTU", "TXN", "AMGN",
    "ISRG", "AMAT", "MU", "LRCX", "KLAC", "PANW", "CDNS", "SNPS", "MRVL",
    "CRWD", "FTNT", "WDAY", "TEAM", "DXCM", "ZS", "ABNB", "IDXX", "BIIB",
    "REGN", "VRTX", "MDLZ", "GILD", "ADP", "CSGP", "EXC", "KDP", "ROST",
    "PAYX", "CPRT", "ODFL", "FAST", "PCAR",
]


class MarketDataFetcher:
    def __init__(self, context_dir: Path):
        self.context_dir = context_dir
        self._alpaca_client = None

    def _get_alpaca_client(self):
        if self._alpaca_client is not None:
            return self._alpaca_client
        if not ALPACA_API_KEY or not ALPACA_API_SECRET:
            return None
        try:
            import requests as _req
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            _orig_req = _req.Session.request
            def _no_verify_req(self, method, url, **kwargs):
                kwargs.setdefault("verify", False)
                return _orig_req(self, method, url, **kwargs)
            _req.Session.request = _no_verify_req

            from alpaca.data.historical import StockHistoricalDataClient
            self._alpaca_client = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_API_SECRET)
            return self._alpaca_client
        except Exception as e:
            logger.warning(f"Alpaca client init failed: {e}")
            return None

    def _fetch_ohlcv_alpaca(self, ticker: str, period: str = "90d") -> pd.DataFrame:
        client = self._get_alpaca_client()
        if client is None:
            return pd.DataFrame()
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame

            # yfinance "90d" = 90 trading days ≈ 126 calendar days; multiply by 1.42 + buffer
            num = int("".join(filter(str.isdigit, period))) if any(c.isdigit() for c in period) else 90
            if "mo" in period:
                cal_days = num * 42
            elif "y" in period:
                cal_days = num * 365
            else:
                cal_days = int(num * 1.42) + 10

            request = StockBarsRequest(
                symbol_or_symbols=ticker,
                timeframe=TimeFrame.Day,
                start=datetime.now() - timedelta(days=cal_days),
                end=datetime.now(),
                feed="iex",
            )
            bars = client.get_stock_bars(request)
            df = bars.df

            if df.empty:
                return pd.DataFrame()

            if isinstance(df.index, pd.MultiIndex):
                df = df.xs(ticker, level="symbol")

            df.index = pd.to_datetime(df.index).tz_localize(None)
            df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            logger.info(f"{ticker}: Alpaca OK ({len(df)} bars)")
            return df
        except Exception as e:
            logger.warning(f"{ticker}: Alpaca failed: {e}")
            return pd.DataFrame()

    def get_sp500_tickers(self, use_cache: bool = True) -> list:
        if use_cache:
            tickers, updated = self._load_cache("sp500")
            if tickers and updated and (datetime.now() - updated).days < 7:
                return tickers

        try:
            import requests
            from bs4 import BeautifulSoup

            resp = requests.get(
                "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", {"id": "constituents"})
            tickers = []
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if cells:
                    t = cells[0].text.strip().replace(".", "-")
                    tickers.append(t)
            if tickers:
                self._save_cache("sp500", tickers)
                return tickers
        except Exception as e:
            logger.warning(f"SP500 Wikipedia scrape failed: {e}. Using fallback list.")

        return FALLBACK_SP500

    def get_ndx100_tickers(self, use_cache: bool = True) -> list:
        if use_cache:
            tickers, updated = self._load_cache("ndx100")
            if tickers and updated and (datetime.now() - updated).days < 7:
                return tickers

        try:
            import requests
            from bs4 import BeautifulSoup

            resp = requests.get(
                "https://en.wikipedia.org/wiki/Nasdaq-100",
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
            )
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", {"id": "constituents"})
            if not table:
                tables = soup.find_all("table", class_="wikitable")
                table = tables[1] if len(tables) > 1 else tables[0] if tables else None
            if table:
                tickers = []
                for row in table.find_all("tr")[1:]:
                    cells = row.find_all("td")
                    if cells:
                        t = cells[0].text.strip().replace(".", "-")
                        if t:
                            tickers.append(t)
                if tickers:
                    self._save_cache("ndx100", tickers)
                    return tickers
        except Exception as e:
            logger.warning(f"NDX100 Wikipedia scrape failed: {e}. Using fallback list.")

        return FALLBACK_NDX100

    def get_universe(self) -> list:
        watchlist = self._load_watchlist()
        if watchlist:
            logger.info(f"Ticker universe: {len(watchlist)} tickers from watchlist")
            return watchlist
        sp500 = self.get_sp500_tickers()
        ndx100 = self.get_ndx100_tickers()
        universe = list(dict.fromkeys(sp500 + ndx100))
        logger.info(f"Ticker universe: {len(universe)} unique tickers")
        return universe

    def _load_watchlist(self) -> list:
        import json
        path = self.context_dir / "watchlist.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tickers = data.get("tickers", [])
            if tickers:
                return tickers
        except Exception:
            pass
        return []

    def fetch_ohlcv(self, ticker: str, period: str = "90d") -> pd.DataFrame:
        # Alpaca primary
        df = self._fetch_ohlcv_alpaca(ticker, period)
        if not df.empty:
            # Patch last bar with live intraday price during market hours
            try:
                intraday = yf.Ticker(ticker).history(period="1d", interval="5m", auto_adjust=True)
                if not intraday.empty:
                    last_price = float(intraday["Close"].iloc[-1])
                    last_vol = float(intraday["Volume"].sum())
                    df.iloc[-1, df.columns.get_loc("Close")] = last_price
                    if last_vol > 0:
                        df.iloc[-1, df.columns.get_loc("Volume")] = last_vol
            except Exception:
                pass
            return df
        # yfinance fallback
        logger.warning(f"{ticker}: Alpaca empty, trying yfinance fallback")
        try:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
            return df
        except Exception as e:
            logger.warning(f"{ticker}: yfinance also failed: {e}")
            return pd.DataFrame()

    def fetch_batch_quotes(self, tickers: list, batch_size: int = 50, force_today: bool = True) -> dict:
        # Alpaca primary — single batch call for all tickers
        results = self._fetch_batch_quotes_alpaca(tickers)
        missing = [t for t in tickers if t not in results]
        if missing:
            logger.warning(f"{len(missing)} tickers missing from Alpaca, falling back to yfinance")
            results.update(self._fetch_batch_quotes_yfinance(missing))
        logger.info(f"Fetched quotes for {len(results)}/{len(tickers)} tickers")
        return results

    def _fetch_batch_quotes_alpaca(self, tickers: list) -> dict:
        client = self._get_alpaca_client()
        if not client or not tickers:
            return {}
        try:
            from alpaca.data.requests import StockBarsRequest
            from alpaca.data.timeframe import TimeFrame

            request = StockBarsRequest(
                symbol_or_symbols=tickers,
                timeframe=TimeFrame.Day,
                start=datetime.now() - timedelta(days=45),
                end=datetime.now(),
                feed="iex",
            )
            df = client.get_stock_bars(request).df
            results = {}
            for ticker in tickers:
                try:
                    t_df = df.xs(ticker, level="symbol").sort_index()
                    if len(t_df) < 2:
                        continue
                    price = float(t_df["close"].iloc[-1])
                    prev = float(t_df["close"].iloc[-2])
                    avg_vol = float(t_df["volume"].tail(20).mean()) if len(t_df) >= 20 else float(t_df["volume"].mean())
                    results[ticker] = {
                        "price": price,
                        "prev_price": prev,
                        "change_pct": (price - prev) / prev * 100,
                        "volume": int(t_df["volume"].iloc[-1]),
                        "avg_vol_20d": int(avg_vol),
                        "high_52w": float(t_df["high"].max()),
                        "low_52w": float(t_df["low"].min()),
                    }
                except Exception:
                    continue
            logger.info(f"Alpaca batch: {len(results)}/{len(tickers)} tickers")
            return results
        except Exception as e:
            logger.warning(f"Alpaca batch failed: {e}")
            return {}

    def _fetch_batch_quotes_yfinance(self, tickers: list, batch_size: int = 50) -> dict:
        results = {}
        batches = [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]
        for i, batch in enumerate(batches):
            try:
                raw = yf.download(batch, period="30d", auto_adjust=True, progress=False, threads=True)
                if raw.empty:
                    continue
                close = raw["Close"] if "Close" in raw else raw.xs("Close", axis=1, level=0)
                volume = raw["Volume"] if "Volume" in raw else raw.xs("Volume", axis=1, level=0)
                high = raw["High"] if "High" in raw else raw.xs("High", axis=1, level=0)
                low = raw["Low"] if "Low" in raw else raw.xs("Low", axis=1, level=0)
                for ticker in batch:
                    try:
                        if ticker not in close.columns:
                            continue
                        t_close = close[ticker].dropna()
                        if len(t_close) < 5:
                            continue
                        price = float(t_close.iloc[-1])
                        prev = float(t_close.iloc[-2]) if len(t_close) > 1 else price
                        t_vol = volume[ticker].dropna()
                        avg_vol = float(t_vol.tail(20).mean()) if len(t_vol) >= 20 else float(t_vol.mean())
                        results[ticker] = {
                            "price": price,
                            "prev_price": prev,
                            "change_pct": (price - prev) / prev * 100,
                            "volume": int(t_vol.iloc[-1]),
                            "avg_vol_20d": int(avg_vol),
                            "high_52w": float(high[ticker].dropna().max()),
                            "low_52w": float(low[ticker].dropna().min()),
                        }
                    except Exception:
                        continue
                if i < len(batches) - 1:
                    time.sleep(2)
            except Exception as e:
                logger.warning(f"yfinance batch {i+1} failed: {e}")
        return results

    def refresh_daily_quotes(self, tickers: list, batch_size: int = 100) -> dict:
        """Force refresh of today's quotes using intraday 5m bars to avoid daily close lag."""
        results = {}
        logger.info(f"Refreshing {len(tickers)} tickers for today's data...")

        for ticker in tickers:
            try:
                data = yf.Ticker(ticker).history(period="1d", interval="5m", auto_adjust=True)
                if data.empty:
                    continue
                price = float(data["Close"].iloc[-1])
                vol = int(data["Volume"].sum())
                h = float(data["High"].max())
                l = float(data["Low"].min())
                results[ticker] = {
                    "price": price,
                    "volume": vol,
                    "high": h,
                    "low": l,
                    "updated": datetime.now().isoformat(),
                }
            except Exception:
                continue

        # legacy batch path kept for reference but replaced above
        if False:
            batches = [tickers[i:i + batch_size] for i in range(0, len(tickers), batch_size)]
            for batch in batches:
                try:
                    data = yf.download(batch, period="1d", progress=False)
                    if data.empty:
                        continue
                except Exception as e:
                    logger.debug(f"Batch refresh failed: {e}")
                    continue

        logger.info(f"Successfully refreshed {len(results)} tickers")
        return results

    def get_market_overview(self) -> MarketConditions:
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            # SPY/QQQ via Alpaca (60d for reliable EMAs); VIX via yfinance (^VIX not on Alpaca)
            spy_price = spy_ema9 = spy_ema21 = spy_ema50 = 0.0
            qqq_price = qqq_ema9 = qqq_ema21 = 0.0
            vix_level = 20.0

            alpaca_ok = False
            client = self._get_alpaca_client()
            if client:
                try:
                    from alpaca.data.requests import StockBarsRequest
                    from alpaca.data.timeframe import TimeFrame
                    req = StockBarsRequest(
                        symbol_or_symbols=["SPY", "QQQ"],
                        timeframe=TimeFrame.Day,
                        start=datetime.now() - timedelta(days=80),
                        end=datetime.now(),
                        feed="iex",
                    )
                    mkt_df = client.get_stock_bars(req).df
                    spy = mkt_df.xs("SPY", level="symbol")["close"].sort_index()
                    qqq = mkt_df.xs("QQQ", level="symbol")["close"].sort_index()
                    spy_price = float(spy.iloc[-1])
                    spy_ema9 = float(spy.ewm(span=9, adjust=False).mean().iloc[-1])
                    spy_ema21 = float(spy.ewm(span=21, adjust=False).mean().iloc[-1])
                    spy_ema50 = float(spy.ewm(span=50, adjust=False).mean().iloc[-1])
                    qqq_price = float(qqq.iloc[-1])
                    qqq_ema9 = float(qqq.ewm(span=9, adjust=False).mean().iloc[-1])
                    qqq_ema21 = float(qqq.ewm(span=21, adjust=False).mean().iloc[-1])
                    alpaca_ok = True
                except Exception as e:
                    logger.warning(f"Alpaca market overview failed: {e}")

            if not alpaca_ok:
                data = yf.download(["SPY", "QQQ"], period="60d", auto_adjust=True, progress=False)
                spy = data["Close"]["SPY"].dropna()
                qqq = data["Close"]["QQQ"].dropna()
                spy_price = float(spy.iloc[-1])
                spy_ema9 = float(spy.ewm(span=9, adjust=False).mean().iloc[-1])
                spy_ema21 = float(spy.ewm(span=21, adjust=False).mean().iloc[-1])
                spy_ema50 = float(spy.ewm(span=50, adjust=False).mean().iloc[-1])
                qqq_price = float(qqq.iloc[-1])
                qqq_ema9 = float(qqq.ewm(span=9, adjust=False).mean().iloc[-1])
                qqq_ema21 = float(qqq.ewm(span=21, adjust=False).mean().iloc[-1])

            # VIX — yfinance only (^VIX not available on Alpaca)
            try:
                vix_data = yf.download("^VIX", period="5d", auto_adjust=True, progress=False)
                vix_level = float(vix_data["Close"].dropna().iloc[-1])
            except Exception:
                pass

            # Crypto via Alpaca CryptoHistoricalDataClient (no API key needed)
            btc_price = btc_chg = eth_price = eth_chg = 0.0
            try:
                from alpaca.data.historical import CryptoHistoricalDataClient
                from alpaca.data.requests import CryptoBarsRequest
                from alpaca.data.timeframe import TimeFrame
                crypto_client = CryptoHistoricalDataClient()
                creq = CryptoBarsRequest(
                    symbol_or_symbols=["BTC/USD", "ETH/USD"],
                    timeframe=TimeFrame.Day,
                    start=datetime.now() - timedelta(days=3),
                    end=datetime.now(),
                )
                c_df = crypto_client.get_crypto_bars(creq).df
                btc = c_df.xs("BTC/USD", level="symbol")["close"].sort_index()
                eth = c_df.xs("ETH/USD", level="symbol")["close"].sort_index()
                if len(btc) >= 2:
                    btc_price = float(btc.iloc[-1])
                    btc_chg = (btc.iloc[-1] - btc.iloc[-2]) / btc.iloc[-2] * 100
                if len(eth) >= 2:
                    eth_price = float(eth.iloc[-1])
                    eth_chg = (eth.iloc[-1] - eth.iloc[-2]) / eth.iloc[-2] * 100
            except Exception:
                try:
                    crypto = yf.download(["BTC-USD", "ETH-USD"], period="2d", auto_adjust=True, progress=False)
                    c_close = crypto["Close"]
                    btc = c_close["BTC-USD"].dropna()
                    eth = c_close["ETH-USD"].dropna()
                    if len(btc) >= 2:
                        btc_price = float(btc.iloc[-1])
                        btc_chg = (btc.iloc[-1] - btc.iloc[-2]) / btc.iloc[-2] * 100
                    if len(eth) >= 2:
                        eth_price = float(eth.iloc[-1])
                        eth_chg = (eth.iloc[-1] - eth.iloc[-2]) / eth.iloc[-2] * 100
                except Exception:
                    pass

            def trend(price, e9, e21, e50):
                if price > e9 > e21 > e50:
                    return "Strong Uptrend"
                elif price > e21 > e50:
                    return "Uptrend"
                elif price < e9 < e21:
                    return "Downtrend"
                else:
                    return "Sideways"

            spy_trend = trend(spy_price, spy_ema9, spy_ema21, spy_ema50)
            qqq_trend = trend(qqq_price, qqq_ema9, qqq_ema21, qqq_price)

            if vix_level < 15:
                regime = "BULLISH — Low fear, risk-on"
            elif vix_level < 20:
                regime = "BULLISH — Normal volatility, favor longs"
            elif vix_level < 30:
                regime = "NEUTRAL — Elevated volatility, be selective"
            else:
                regime = "BEARISH — High fear, reduce exposure"

            return MarketConditions(
                date=today,
                spy_trend=spy_trend,
                qqq_trend=qqq_trend,
                vix_level=round(vix_level, 2),
                regime=regime,
                spy_price=round(float(spy_price), 2),
                qqq_price=round(float(qqq_price), 2),
                btc_price=round(btc_price, 0),
                btc_change_pct=round(btc_chg, 2),
                eth_price=round(eth_price, 0),
                eth_change_pct=round(eth_chg, 2),
            )

        except Exception as e:
            logger.error(f"Failed to get market overview: {e}")
            return MarketConditions(
                date=today,
                spy_trend="Unknown",
                qqq_trend="Unknown",
                vix_level=20.0,
                regime="NEUTRAL — Data unavailable",
            )

    def _load_cache(self, key: str) -> tuple:
        import json
        path = self.context_dir / f"ticker_universe_{key}.json"
        if not path.exists():
            return [], None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data["tickers"], datetime.fromisoformat(data["updated"])
        except Exception:
            return [], None

    def _save_cache(self, key: str, tickers: list) -> None:
        import json, os
        path = self.context_dir / f"ticker_universe_{key}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"tickers": tickers, "updated": datetime.now().isoformat()}, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)
