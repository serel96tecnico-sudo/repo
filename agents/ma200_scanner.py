"""
MA200 Scanner — escanea la watchlist en busca de tickers que cotizan cerca
de su media móvil de 200 sesiones diaria (SMA200), zona de soporte/resistencia
estructural de la tendencia mayor.

100% determinista (reutiliza data.indicators.ma200_position) — no llama a Claude,
sin coste de tokens. Cada ticker requiere una descarga OHLCV diaria larga
(MA200_DAILY_PERIOD, ~6 años) vía Alpaca/yfinance.
"""
import json
from datetime import datetime

from config import (
    CONTEXT_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LOGS_DIR,
    MA200_PERIOD, MA200_DAILY_PERIOD,
)
from data.indicators import ma200_position
from utils.logger import get_logger

logger = get_logger("MA200Scanner", LOGS_DIR)

MA200_PROXIMITY_PCT = 2.0   # % de distancia máxima al MA200 para considerarlo "cerca"
MIN_PRICE = 2.0             # filtra penny stocks


def _load_watchlist() -> list:
    path = CONTEXT_DIR / "watchlist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("tickers", [])
    except Exception as e:
        logger.error(f"No se pudo leer watchlist.json: {e}")
        return []


def _load_portfolio_tickers() -> set:
    path = CONTEXT_DIR / "portfolio.json"
    try:
        pf = json.loads(path.read_text(encoding="utf-8"))
        held = [p["ticker"] for p in pf.get("acciones", []) + pf.get("etfs", [])]
        return set(held)
    except Exception:
        return set()


def _scan_ticker(ticker: str) -> dict:
    try:
        from data.market_data import MarketDataFetcher
        fetcher = MarketDataFetcher(context_dir=CONTEXT_DIR)
        df = fetcher.fetch_ohlcv(ticker, period=MA200_DAILY_PERIOD, timeframe="day")
        if df is None or df.empty:
            return {}
        price = float(df["Close"].iloc[-1])
        if price < MIN_PRICE:
            return {}
        ma = ma200_position(df, MA200_PERIOD)
        if ma.get("ma200") is None:
            return {}
        return {"ticker": ticker, "price": price, **ma}
    except Exception as e:
        logger.warning(f"{ticker}: error MA200 — {e}")
        return {}


def _build_message(hits: list, proximity_pct: float) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"SCAN MA200 [D1, ±{proximity_pct:.1f}%] — {now}", ""]
    for r in sorted(hits, key=lambda r: abs(r["price_vs_ma200_pct"])):
        side = "sobre" if r["above"] else "bajo"
        slope = "subiendo" if r["slope_up"] else "bajando"
        lines.append(
            f"{r['ticker']}: ${r['price']:.2f}  MA200 ${r['ma200']:.2f}  "
            f"({r['price_vs_ma200_pct']:+.2f}% {side}, MA200 {slope})"
        )
    return "\n".join(lines)


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        for chunk in [text[i:i + 4096] for i in range(0, len(text), 4096)]:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk}, timeout=10)
    except Exception as e:
        logger.warning(f"Telegram error: {e}")


def run_ma200_scanner(
    tickers: list = None,
    skip_held: bool = True,
    notify: bool = True,
    proximity_pct: float = MA200_PROXIMITY_PCT,
) -> list:
    """
    Escanea tickers en busca de precio a ±proximity_pct% de su MA200 diaria.

    Args:
        tickers:       lista de tickers a escanear (None = toda la watchlist)
        skip_held:      omitir tickers ya en cartera
        notify:         enviar alerta Telegram si hay resultados
        proximity_pct:  distancia máxima (%) al MA200 para incluir el ticker

    Returns:
        lista de dicts (ticker, price, ma200, price_vs_ma200_pct, above, slope_up),
        ordenada por cercanía a la MA200.
    """
    if tickers is None:
        tickers = _load_watchlist()

    held = _load_portfolio_tickers() if skip_held else set()
    scan_list = [t for t in tickers if t not in held]

    logger.info(f"MA200Scanner: escaneando {len(scan_list)} tickers "
                f"({len(held)} en cartera omitidos)")

    hits = []
    for ticker in scan_list:
        result = _scan_ticker(ticker)
        if not result:
            continue
        if abs(result["price_vs_ma200_pct"]) <= proximity_pct:
            hits.append(result)
            logger.info(f"{ticker}: {result['price_vs_ma200_pct']:+.2f}% de su MA200")

    hits.sort(key=lambda r: abs(r["price_vs_ma200_pct"]))
    logger.info(f"MA200Scanner: {len(hits)} tickers cerca de su MA200")

    if hits and notify:
        _send_telegram(_build_message(hits, proximity_pct))

    return hits
