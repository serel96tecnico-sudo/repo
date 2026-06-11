"""
Entry Scanner — escanea la watchlist en busca de señales de entrada
y envía alertas por Telegram cuando detecta señales confluentes.

Señales LONG:
  - EMA_CROSS_UP:     precio cruza por encima de EMA9
  - MACD_BULL_CROSS:  MACD cruza por encima de señal
  - RSI_RECOVERY:     RSI sube por encima de 40 (salida de sobreventa)
  - PULLBACK_EMA:     precio apoya en EMA9/21 en tendencia alcista
  - BREAKOUT:         precio rompe resistencia 20d con volumen 3x+
  - VOLUME_SURGE_UP:  volumen 3x+ en día positivo

Señales SHORT (solo B2):
  - EMA_CROSS_DOWN:   precio cruza por debajo de EMA9
  - MACD_BEAR_CROSS:  MACD cruza por debajo de señal
  - RSI_REJECT:       RSI > 70 y cayendo bajo 70
  - BREAKDOWN:        precio rompe soporte 20d con volumen 3x+
  - BELOW_SMA20:      precio bajo SMA20 y SMA20 cayendo
  - VOLUME_SURGE_DN:  volumen 2x+ en día negativo (más sensible que long)
"""
import json
from datetime import datetime
from pathlib import Path

from config import (
    CONTEXT_DIR, OUTPUT_DIR, LOGS_DIR,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from data.indicators import (
    calculate_rsi, calculate_macd, calculate_ema, calculate_atr, calculate_adx,
)
from utils.logger import get_logger

logger = get_logger("EntryScanner", LOGS_DIR)

# Temporalidad del scanner
SCAN_TIMEFRAME    = "4hour"   # "day" | "4hour" | "hour"
SCAN_DAYS         = 45        # días de historial (45d ≈ 72 velas H4, suficiente para indicadores)

# Umbrales
MIN_SIGNALS_LONG  = 2      # señales mínimas para alertar long
MIN_SIGNALS_SHORT = 2      # señales mínimas para alertar short
MIN_SIGNALS       = MIN_SIGNALS_LONG  # alias para la firma de run_entry_scanner
RSI_OVERSOLD      = 40
RSI_OVERBOUGHT    = 70
RSI_MAX_LONG      = 74     # no alertar longs si RSI > este valor (sobrecomprado)
RSI_MIN_SHORT     = 35     # no alertar shorts si RSI ya en sobreventa profunda
VOLUME_MULT       = 2.0    # veces el promedio para breakout/surge (H4: umbral reducido)
VOLUME_MULT_DN    = 2.0    # veces el promedio para señales bajistas
ADX_TREND_MIN     = 20     # ADX mínimo para confirmar que hay tendencia (filtra rangos laterales)
BREAKOUT_LOOKBACK = 20     # velas para calcular resistencia/soporte
MIN_PRICE         = 2.0    # precio mínimo (filtra penny stocks)
# H4: volumen por vela ≈ 1/6 del diario → umbral ajustado
MIN_AVG_VOL       = 50_000 if SCAN_TIMEFRAME != "day" else 300_000

# Filtro de tendencia diaria (macro): evita longs contra-tendencia y shorts contra-tendencia.
# Se obtiene una vela diaria aparte y se compara precio vs EMA50 diaria.
DAILY_TREND_FILTER = True   # exigir alineación con la tendencia diaria
DAILY_TREND_DAYS   = 120    # historial diario para EMA50 (necesita >50 velas)
DAILY_EMA_LEN      = 50     # EMA de referencia para la tendencia diaria

# Señales ancla long — sin al menos una, no se alerta long
ANCHOR_SIGNALS_LONG = {
    "MACD_BULL_CROSS", "RSI_RECOVERY", "BREAKOUT", "PULLBACK_EMA",
}
# Señales ancla short — sin al menos una, no se alerta short
ANCHOR_SIGNALS_SHORT = {
    "MACD_BEAR_CROSS", "RSI_REJECT", "BREAKDOWN", "BELOW_SMA20",
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


def _fetch_ohlcv(ticker: str, days: int = SCAN_DAYS):
    try:
        from data.market_data import MarketDataFetcher
        fetcher = MarketDataFetcher(context_dir=CONTEXT_DIR)
        df = fetcher.fetch_ohlcv(ticker, period=f"{days}d", timeframe=SCAN_TIMEFRAME)
        if df is None or len(df) < 25:
            return None
        return df
    except Exception as e:
        logger.warning(f"{ticker}: error obteniendo OHLCV — {e}")
        return None


def _daily_trend(ticker: str) -> str:
    """
    Devuelve la tendencia diaria del ticker: 'up', 'down' o 'neutral'.
    Compara el precio diario con su EMA50 y la pendiente de la EMA.
    'neutral' si no hay datos suficientes (no bloquea señales).
    """
    if not DAILY_TREND_FILTER:
        return "neutral"
    try:
        from data.market_data import MarketDataFetcher
        fetcher = MarketDataFetcher(context_dir=CONTEXT_DIR)
        df = fetcher.fetch_ohlcv(ticker, period=f"{DAILY_TREND_DAYS}d", timeframe="day")
        if df is None or len(df) < DAILY_EMA_LEN + 5:
            return "neutral"
        close = df["Close"].squeeze()
        ema = calculate_ema(close, DAILY_EMA_LEN)
        price = float(close.iloc[-1])
        ema_now = float(ema.iloc[-1])
        ema_prev = float(ema.iloc[-5])
        slope_up = ema_now > ema_prev
        if price > ema_now and slope_up:
            return "up"
        if price < ema_now and not slope_up:
            return "down"
        return "neutral"
    except Exception as e:
        logger.debug(f"{ticker}: tendencia diaria no disponible — {e}")
        return "neutral"


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
    open_  = df["Open"].squeeze()

    price      = float(close.iloc[-1])
    prev_price = float(close.iloc[-2])
    open_now   = float(open_.iloc[-1])
    low_now    = float(low.iloc[-1])
    bullish_candle = price > open_now  # vela actual cierra por encima de apertura

    ema9       = calculate_ema(close, 9)
    ema21      = calculate_ema(close, 21)
    sma20      = close.rolling(20).mean()
    ema9_now   = float(ema9.iloc[-1])
    ema9_prev  = float(ema9.iloc[-2])
    ema21_now  = float(ema21.iloc[-1])
    sma20_now  = float(sma20.iloc[-1])
    sma20_prev = float(sma20.iloc[-2])

    rsi_series = calculate_rsi(close)
    rsi_now    = float(rsi_series.iloc[-1])
    rsi_prev   = float(rsi_series.iloc[-2])

    macd_line, signal_line, _ = calculate_macd(close)
    macd_now   = float(macd_line.iloc[-1])
    macd_prev  = float(macd_line.iloc[-2])
    sig_now    = float(signal_line.iloc[-1])
    sig_prev   = float(signal_line.iloc[-2])

    avg_vol    = float(vol.iloc[-100:-1].mean())  # ~20 días en H4
    last_vol   = float(vol.iloc[-1])
    vol_ratio  = last_vol / avg_vol if avg_vol > 0 else 0

    # Pendiente EMA como proxy de dirección de tendencia
    ema9_slope = (ema9_now - float(ema9.iloc[-6])) / 5  # pendiente 5 velas

    # ADX real para medir fuerza de tendencia (filtra rangos laterales)
    try:
        adx_series = calculate_adx(high, low, close)
        adx_now = float(adx_series.iloc[-1])
    except Exception:
        adx_now = 0.0
    trending = adx_now >= ADX_TREND_MIN

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

    # 4. Pullback a EMA9/21 en tendencia alcista con vela de giro
    #    Exige: uptrend + la vela TOCÓ la EMA (low por debajo) + cierre alcista (giro)
    in_uptrend = ema9_now > ema21_now and ema9_slope > 0
    touched_ema9  = low_now <= ema9_now * 1.005 and price >= ema9_now * 0.99
    touched_ema21 = low_now <= ema21_now * 1.005 and price >= ema21_now * 0.99
    if in_uptrend and bullish_candle and (touched_ema9 or touched_ema21):
        ema_ref = "EMA9" if touched_ema9 else "EMA21"
        ema_val = ema9_now if touched_ema9 else ema21_now
        long_signals.append(("PULLBACK_EMA", f"rebote en {ema_ref} ${ema_val:.2f} (vela de giro, uptrend)"))

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

    # 5. Precio bajo SMA20 y SMA20 cayendo
    if price < sma20_now and sma20_now < sma20_prev:
        short_signals.append(("BELOW_SMA20", f"${price:.2f} bajo SMA20 ${sma20_now:.2f} (SMA cayendo)"))

    # 6. Volumen bajista (2x+ en día negativo — umbral menor que long)
    if price < prev_price and vol_ratio >= VOLUME_MULT_DN:
        short_signals.append(("VOLUME_SURGE_DN", f"volumen {vol_ratio:.1f}x en día -{abs(price - prev_price):.2f}"))

    # ATR para SL/TP sugeridos (sobre la temporalidad del scan)
    try:
        atr_series = calculate_atr(high, low, close)
        atr_now = float(atr_series.iloc[-1])
    except Exception:
        atr_now = 0.0

    return {
        "ticker":        ticker,
        "price":         price,
        "rsi":           round(rsi_now, 1),
        "vol_ratio":     round(vol_ratio, 1),
        "adx":           round(adx_now, 1),
        "trending":      trending,
        "atr":           round(atr_now, 4),
        "long_signals":  long_signals,
        "short_signals": short_signals,
    }


def _sl_tp_line(price: float, atr: float, direction: str) -> str:
    """Calcula SL/TP sugeridos con ATR y el R:R resultante (objetivo a 2x riesgo)."""
    from config import ATR_STOP_MULTIPLIER
    if atr <= 0:
        return ""
    risk = ATR_STOP_MULTIPLIER * atr
    if direction == "long":
        sl = price - risk
        tp = price + 2.0 * risk
    else:
        sl = price + risk
        tp = price - 2.0 * risk
    return f"  SL ${sl:.2f} / TP ${tp:.2f} (R:R 2.0, ATR ${atr:.2f})"


def _trend_tag(r: dict) -> str:
    t = r.get("daily_trend")
    if t == "up":
        return "  [D1 alcista ✓]"
    if t == "down":
        return "  [D1 bajista ✓]"
    return ""


def _build_message(alerts: list) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    tf_label = {"day": "D1", "4hour": "H4", "hour": "H1"}.get(SCAN_TIMEFRAME, SCAN_TIMEFRAME)
    lines = [f"ENTRY SCANNER [{tf_label}] — {now}", ""]

    for r in alerts:
        ticker = r["ticker"]
        price  = r["price"]
        adx    = r.get("adx", 0)
        atr    = r.get("atr", 0)
        trend_tag = _trend_tag(r)

        if r["long_signals"]:
            severity = "FUERTE" if len(r["long_signals"]) >= 3 else "SETUP"
            lines.append(f"LONG {severity} {ticker} @ ${price:.2f}  RSI {r['rsi']}  Vol {r['vol_ratio']}x  ADX {adx}{trend_tag}")
            for name, detail in r["long_signals"]:
                lines.append(f"  + {name}: {detail}")
            sltp = _sl_tp_line(price, atr, "long")
            if sltp:
                lines.append(sltp)

        if r["short_signals"]:
            severity = "FUERTE" if len(r["short_signals"]) >= 3 else "SETUP"
            lines.append(f"SHORT {severity} {ticker} @ ${price:.2f}  RSI {r['rsi']}  Vol {r['vol_ratio']}x  ADX {adx}{trend_tag}")
            for name, detail in r["short_signals"]:
                lines.append(f"  - {name}: {detail}")
            sltp = _sl_tp_line(price, atr, "short")
            if sltp:
                lines.append(sltp)

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

            # Filtro RSI: no alertar shorts si ya en sobreventa profunda
            if rsi < RSI_MIN_SHORT:
                short_sigs = []

            # Filtro de tendencia diaria (macro): evita operar contra la tendencia
            # del marco superior — principal causa de stop-outs en swing.
            if DAILY_TREND_FILTER and (long_sigs or short_sigs):
                trend = _daily_trend(ticker)
                result["daily_trend"] = trend
                if trend == "down":
                    long_sigs = []   # no longs en tendencia diaria bajista
                elif trend == "up":
                    short_sigs = []  # no shorts en tendencia diaria alcista

            # Filtro ancla: exigir al menos una señal de calidad
            if not any(n in ANCHOR_SIGNALS_LONG for n, _ in long_sigs):
                long_sigs = []
            if not any(n in ANCHOR_SIGNALS_SHORT for n, _ in short_sigs):
                short_sigs = []

            # Filtro contradicción: si ambas superan umbral, solo mostrar la más fuerte
            long_ok  = len(long_sigs)  >= min_signals
            short_ok = len(short_sigs) >= MIN_SIGNALS_SHORT
            if long_ok and short_ok:
                if len(long_sigs) > len(short_sigs):
                    short_sigs = []
                else:
                    long_sigs = []

            has_long  = len(long_sigs)  >= min_signals
            has_short = len(short_sigs) >= MIN_SIGNALS_SHORT

            # Limpiar señales que no pasan el umbral para que no se muestren
            result["long_signals"]  = long_sigs  if has_long  else []
            result["short_signals"] = short_sigs if has_short else []

            if has_long or has_short:
                alerts.append(result)
                logger.info(
                    f"{ticker}: {len(long_sigs)}L "
                    f"{len(short_sigs)}S señales @ ${result['price']:.2f}"
                )
            else:
                logger.debug(f"{ticker}: filtrado ({len(long_sigs)}L {len(short_sigs)}S, ancla o RSI)")

        except Exception as e:
            logger.warning(f"{ticker}: error en scan — {e}")

    logger.info(f"EntryScanner: {len(alerts)} tickers con señales")

    if alerts and notify:
        msg = _build_message(alerts)
        _send_telegram(msg)

    return alerts
