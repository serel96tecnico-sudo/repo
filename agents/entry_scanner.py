"""
Entry Scanner — escanea la watchlist en busca de señales de entrada
y envía alertas por Telegram cuando detecta 2+ señales confluentes.

Señales LONG:
  - EMA_CROSS_UP:     precio cruza por encima de EMA9
  - MACD_BULL_CROSS:  MACD cruza por encima de señal
  - RSI_RECOVERY:     RSI sube por encima de 40 (salida de sobreventa)
  - PULLBACK_EMA:     precio apoya en EMA9/21 en tendencia alcista (ADX > 25)
  - BREAKOUT:         precio rompe resistencia 20d con volumen 2x+
  - VOLUME_SURGE_UP:  volumen 2x+ en día positivo

Señales SHORT (solo B2):
  - EMA_CROSS_DOWN:   precio cruza por debajo de EMA9
  - MACD_BEAR_CROSS:  MACD cruza por debajo de señal
  - RSI_REJECT:       RSI > 70 y cayendo
  - BREAKDOWN:        precio rompe soporte 20d con volumen 2x+
"""
import json
from datetime import datetime
from pathlib import Path

from config import (
    CONTEXT_DIR, OUTPUT_DIR, LOGS_DIR,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from data.indicators import (
    calculate_rsi, calculate_macd, calculate_ema, calculate_atr,
)
from utils.logger import get_logger

logger = get_logger("EntryScanner", LOGS_DIR)

# Umbrales
MIN_SIGNALS       = 3      # señales mínimas para alertar
RSI_OVERSOLD      = 40
RSI_OVERBOUGHT    = 70
RSI_MAX_LONG      = 74     # no alertar longs si RSI > este valor (sobrecomprado)
VOLUME_MULT       = 3.0    # veces el promedio para considerarlo spike (más exigente)
ADX_TREND_MIN     = 25     # ADX mínimo para confirmar tendencia
BREAKOUT_LOOKBACK = 20     # días para calcular resistencia/soporte
MIN_PRICE         = 2.0    # precio mínimo (filtra penny stocks)
MIN_AVG_VOL       = 300_000  # volumen medio mínimo (liquidez)

# Señales que cuentan como "ancla" — sin al menos una, no se alerta
ANCHOR_SIGNALS = {
    "MACD_BULL_CROSS", "MACD_BEAR_CROSS",
    "RSI_RECOVERY", "RSI_REJECT",
    "BREAKOUT", "BREAKDOWN",
    "PULLBACK_EMA",
}


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


def _fetch_ohlcv(ticker: str, days: int = 60):
    try:
        from data.market_data import MarketDataFetcher
        fetcher = MarketDataFetcher(context_dir=CONTEXT_DIR)
        df = fetcher.fetch_ohlcv(ticker, period=f"{days}d")
        if df is None or len(df) < 25:
            return None
        return df
    except Exception as e:
        logger.warning(f"{ticker}: error obteniendo OHLCV — {e}")
        return None


def _scan_ticker(ticker: str) -> dict:
    """
    Analiza un ticker y devuelve dict con señales long y short detectadas.
    """
    df = _fetch_ohlcv(ticker)
    if df is None:
        return {}

    close  = df["Close"].squeeze()
    high   = df["High"].squeeze()
    low    = df["Low"].squeeze()
    vol    = df["Volume"].squeeze()

    price      = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])

    ema9       = calculate_ema(close, 9)
    ema21      = calculate_ema(close, 21)
    ema9_now   = float(ema9.iloc[-1])
    ema9_prev  = float(ema9.iloc[-2])
    ema21_now  = float(ema21.iloc[-1])

    rsi_series = calculate_rsi(close)
    rsi_now    = float(rsi_series.iloc[-1])
    rsi_prev   = float(rsi_series.iloc[-2])

    macd_line, signal_line, _ = calculate_macd(close)
    macd_now   = float(macd_line.iloc[-1])
    macd_prev  = float(macd_line.iloc[-2])
    sig_now    = float(signal_line.iloc[-1])
    sig_prev   = float(signal_line.iloc[-2])

    avg_vol    = float(vol.iloc[-21:-1].mean())
    last_vol   = float(vol.iloc[-1])
    vol_ratio  = last_vol / avg_vol if avg_vol > 0 else 0

    # ATR para ADX proxy — usamos pendiente EMA como proxy de tendencia
    ema9_slope = (ema9_now - float(ema9.iloc[-6])) / 5  # pendiente 5 días

    # Resistencia y soporte recientes
    resistance = float(high.iloc[-BREAKOUT_LOOKBACK:-1].max())
    support    = float(low.iloc[-BREAKOUT_LOOKBACK:-1].min())

    # ── Filtros básicos de calidad ────────────────────────────
    if price < MIN_PRICE:
        return {}
    if avg_vol < MIN_AVG_VOL:
        return {}

    long_signals  = []
    short_signals = []

    # ── SEÑALES LONG ──────────────────────────────────────────
    # 1. Precio cruza por encima de EMA9
    if prev_price < ema9_prev and price >= ema9_now:
        long_signals.append(("EMA_CROSS_UP", f"precio ${price:.2f} cruzó EMA9 ${ema9_now:.2f}"))

    # 2. MACD cruce alcista
    if macd_prev < sig_prev and macd_now >= sig_now:
        long_signals.append(("MACD_BULL_CROSS", f"MACD {macd_now:.3f} cruzó señal {sig_now:.3f}"))

    # 3. RSI sale de sobreventa
    if rsi_prev < RSI_OVERSOLD and rsi_now >= RSI_OVERSOLD:
        long_signals.append(("RSI_RECOVERY", f"RSI {rsi_now:.1f} salió de sobreventa (antes {rsi_prev:.1f})"))

    # 4. Pullback a EMA9/21 en tendencia alcista
    in_uptrend = ema9_now > ema21_now and ema9_slope > 0
    near_ema9  = abs(price - ema9_now) / ema9_now < 0.015   # dentro del 1.5%
    near_ema21 = abs(price - ema21_now) / ema21_now < 0.015
    if in_uptrend and (near_ema9 or near_ema21):
        ema_ref = "EMA9" if near_ema9 else "EMA21"
        ema_val = ema9_now if near_ema9 else ema21_now
        long_signals.append(("PULLBACK_EMA", f"pullback a {ema_ref} ${ema_val:.2f} en uptrend"))

    # 5. Breakout sobre resistencia 20d con volumen
    if price > resistance and vol_ratio >= VOLUME_MULT:
        long_signals.append(("BREAKOUT", f"ruptura ${price:.2f} sobre resistencia ${resistance:.2f} (vol {vol_ratio:.1f}x)"))

    # 6. Volumen alcista (2x+ en día positivo)
    if price > prev_price and vol_ratio >= VOLUME_MULT:
        long_signals.append(("VOLUME_SURGE_UP", f"volumen {vol_ratio:.1f}x en día +{price - prev_price:.2f}"))

    # ── SEÑALES SHORT ─────────────────────────────────────────
    # 1. Precio cruza por debajo de EMA9
    if prev_price > ema9_prev and price <= ema9_now:
        short_signals.append(("EMA_CROSS_DOWN", f"precio ${price:.2f} cruzó bajo EMA9 ${ema9_now:.2f}"))

    # 2. MACD cruce bajista
    if macd_prev > sig_prev and macd_now <= sig_now:
        short_signals.append(("MACD_BEAR_CROSS", f"MACD {macd_now:.3f} cruzó bajo señal {sig_now:.3f}"))

    # 3. RSI rechaza desde sobrecompra
    if rsi_prev >= RSI_OVERBOUGHT and rsi_now < RSI_OVERBOUGHT:
        short_signals.append(("RSI_REJECT", f"RSI {rsi_now:.1f} rechazado desde sobrecompra (antes {rsi_prev:.1f})"))

    # 4. Breakdown bajo soporte 20d con volumen
    if price < support and vol_ratio >= VOLUME_MULT:
        short_signals.append(("BREAKDOWN", f"ruptura ${price:.2f} bajo soporte ${support:.2f} (vol {vol_ratio:.1f}x)"))

    return {
        "ticker":        ticker,
        "price":         price,
        "rsi":           round(rsi_now, 1),
        "vol_ratio":     round(vol_ratio, 1),
        "long_signals":  long_signals,
        "short_signals": short_signals,
    }


def _build_message(alerts: list) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"ENTRY SCANNER — {now}", ""]

    for r in alerts:
        ticker = r["ticker"]
        price  = r["price"]

        if r["long_signals"]:
            severity = "FUERTE" if len(r["long_signals"]) >= 3 else "SETUP"
            lines.append(f"LONG {severity} {ticker} @ ${price:.2f}  RSI {r['rsi']}  Vol {r['vol_ratio']}x")
            for name, detail in r["long_signals"]:
                lines.append(f"  + {name}: {detail}")

        if r["short_signals"]:
            severity = "FUERTE" if len(r["short_signals"]) >= 3 else "SETUP"
            lines.append(f"SHORT {severity} {ticker} @ ${price:.2f}  RSI {r['rsi']}  Vol {r['vol_ratio']}x")
            for name, detail in r["short_signals"]:
                lines.append(f"  - {name}: {detail}")

        lines.append("")

    return "\n".join(lines)


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
                          timeout=10, verify=False)
    except Exception as e:
        logger.warning(f"Telegram error: {e}")


def run_entry_scanner(
    tickers: list = None,
    skip_held: bool = True,
    notify: bool = True,
    min_signals: int = MIN_SIGNALS,
) -> list:
    """
    Escanea tickers en busca de señales de entrada.

    Args:
        tickers:    lista de tickers a escanear (None = toda la watchlist)
        skip_held:  omitir tickers ya en cartera
        notify:     enviar alerta Telegram si hay señales
        min_signals: mínimo de señales para incluir en alerta

    Returns:
        lista de dicts con resultados que superan el umbral
    """
    if tickers is None:
        tickers = _load_watchlist()

    held = _load_portfolio_tickers() if skip_held else set()
    scan_list = [t for t in tickers if t not in held]

    logger.info(f"EntryScanner: escaneando {len(scan_list)} tickers "
                f"({len(held)} en cartera omitidos)")

    alerts = []
    for ticker in scan_list:
        try:
            result = _scan_ticker(ticker)
            if not result:
                continue

            rsi = result.get("rsi", 50)
            long_sigs  = result["long_signals"]
            short_sigs = result["short_signals"]

            # Filtro RSI: no alertar longs si muy sobrecomprado
            if rsi > RSI_MAX_LONG:
                long_sigs = []

            # Filtro ancla: exigir al menos una señal de calidad
            has_long_anchor  = any(n in ANCHOR_SIGNALS for n, _ in long_sigs)
            has_short_anchor = any(n in ANCHOR_SIGNALS for n, _ in short_sigs)
            if not has_long_anchor:
                long_sigs = []
            if not has_short_anchor:
                short_sigs = []

            result["long_signals"]  = long_sigs
            result["short_signals"] = short_sigs

            total = len(long_sigs) + len(short_sigs)
            if total >= min_signals:
                alerts.append(result)
                logger.info(
                    f"{ticker}: {len(long_sigs)}L "
                    f"{len(short_sigs)}S señales @ ${result['price']:.2f}"
                )
            else:
                logger.debug(f"{ticker}: filtrado ({total} señales, ancla o RSI)")

        except Exception as e:
            logger.warning(f"{ticker}: error en scan — {e}")

    logger.info(f"EntryScanner: {len(alerts)} tickers con señales")

    if alerts and notify:
        msg = _build_message(alerts)
        _send_telegram(msg)

    return alerts
