"""
Portfolio Watchdog — detecta señales de giro en posiciones abiertas
y envía alerta por Telegram si encuentra riesgo.
"""
import json
from datetime import datetime
from pathlib import Path

from config import (
    CONTEXT_DIR, OUTPUT_DIR, LOGS_DIR, EARNINGS_BLOCK_DAYS,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from data.indicators import (
    calculate_rsi, calculate_macd, calculate_ema, calculate_atr,
)
from utils.logger import get_logger

logger = get_logger("PortfolioWatchdog", LOGS_DIR)

# Señales de alerta
SIGNAL_STOP_PROXIMITY   = "STOP CERCANO"       # precio dentro del 3% del SL
SIGNAL_TP_PROXIMITY     = "TP CERCANO"         # precio dentro del 3% del TP
SIGNAL_BELOW_BEP        = "BAJO BEP"           # precio bajo precio de entrada
SIGNAL_RSI_BEARISH      = "RSI BAJISTA"        # RSI < 40 y cayendo
SIGNAL_MACD_CROSS       = "MACD CRUCE BAJISTA" # MACD cruzó bajo señal
SIGNAL_BELOW_EMA20      = "BAJO EMA20"         # precio bajo EMA20 + EMA20 cayendo
SIGNAL_VOLUME_SPIKE_DN  = "VOLUMEN BAJISTA"    # volumen 2x en día negativo
SIGNAL_NO_STOP          = "SIN STOP"           # posición abierta sin stop-loss colocado
SIGNAL_EARNINGS_SOON    = "EARNINGS PRONTO"    # reporta resultados en <= EARNINGS_BLOCK_DAYS

SL_PROXIMITY_PCT = 0.03   # alertar si precio está dentro del 3% del SL
TP_PROXIMITY_PCT = 0.03   # alertar si precio está dentro del 3% del TP


def _load_portfolio() -> dict:
    path = CONTEXT_DIR / "portfolio.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"No se pudo leer portfolio.json: {e}")
        return {}


def _fetch_ohlcv(ticker: str, days: int = 60):
    try:
        from data.market_data import MarketDataFetcher
        fetcher = MarketDataFetcher(context_dir=CONTEXT_DIR)
        df = fetcher.fetch_ohlcv(ticker, period=f"{days}d")
        if df is None or len(df) < 20:
            return None
        return df
    except Exception as e:
        logger.warning(f"{ticker}: error obteniendo OHLCV — {e}")
        return None


def _check_earnings(ticker: str) -> tuple:
    """Días hasta el próximo earnings, vía Finviz. (None, None) si no se pudo
    determinar (fallo de red, ticker no soportado, etc.) — un hueco de datos
    no genera alerta, no la suprime falsamente."""
    try:
        from agents.fundamental_analyst import FundamentalAnalyst
        data = FundamentalAnalyst._scrape_finviz_quote(ticker)
        earnings_str = (data or {}).get("Earnings", "-") or "-"
        days = FundamentalAnalyst._parse_earnings_days(earnings_str)
        if days >= 999:
            return None, None
        return days, earnings_str
    except Exception as e:
        logger.warning(f"{ticker}: error consultando earnings — {e}")
        return None, None


def _analyze_position(pos: dict) -> list:
    """Devuelve lista de señales de alerta para una posición."""
    ticker = pos.get("ticker", "?")
    bep = pos.get("bep_usd") or pos.get("bep_eur") or 0
    sl = pos.get("stop_loss")
    tp = pos.get("take_profit")
    qty = pos.get("cantidad", 0) or 0
    direccion = pos.get("direccion", "long")

    signals = []

    # Riesgos estructurales: no dependen de tener datos de mercado ni de la
    # dirección de la posición, así que se comprueban primero y sobreviven
    # aunque falle la descarga de OHLCV más abajo.

    # 0a. Posición abierta sin stop-loss colocado — el patrón de pérdida más
    # caro y más repetido del historial de esta cartera (INTC, RXT, TSM...).
    if qty > 0 and not sl:
        signals.append((SIGNAL_NO_STOP, f"{qty} acc. sin stop-loss colocado"))

    # 0b. Earnings dentro de la misma ventana que bloquea entradas nuevas
    # (EARNINGS_BLOCK_DAYS en fundamental_analyst) — esa regla nunca se
    # aplicaba a posiciones ya abiertas, así que un evento binario podía
    # atravesar un stop sin aviso previo (caso UNH, 15/07).
    days_to_earn, earnings_str = _check_earnings(ticker)
    if days_to_earn is not None and 0 <= days_to_earn <= EARNINGS_BLOCK_DAYS:
        cuando = "mañana" if days_to_earn == 1 else f"en {days_to_earn}d" if days_to_earn else "HOY"
        signals.append((SIGNAL_EARNINGS_SOON, f"reporta {cuando} ({earnings_str}) — el stop puede saltar por gap"))

    # Solo aplica análisis técnico bajista para posiciones LONG
    if direccion != "long":
        return signals

    df = _fetch_ohlcv(ticker)
    if df is None:
        return signals

    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    vol   = df["Volume"].squeeze()

    price     = float(close.iloc[-1])
    rsi_series = calculate_rsi(close)
    rsi       = float(rsi_series.iloc[-1])
    rsi_prev  = float(rsi_series.iloc[-2])
    ema20     = float(calculate_ema(close, 20).iloc[-1])
    ema20_prev = float(calculate_ema(close, 20).iloc[-2])
    macd_line, signal_line, _ = calculate_macd(close)
    macd_now  = float(macd_line.iloc[-1])
    macd_prev = float(macd_line.iloc[-2])
    sig_now   = float(signal_line.iloc[-1])
    sig_prev  = float(signal_line.iloc[-2])
    avg_vol   = float(vol.iloc[-20:-1].mean())
    last_vol  = float(vol.iloc[-1])
    prev_close = float(close.iloc[-2])
    last_chg_pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0

    # 1. Stop cercano (dentro del 3%)
    if sl and price > 0:
        pct_to_sl = (price - sl) / price
        if 0 <= pct_to_sl < SL_PROXIMITY_PCT:
            signals.append((SIGNAL_STOP_PROXIMITY, f"precio ${price:.2f} a {pct_to_sl*100:.1f}% del SL ${sl}"))

    # 1b. TP cercano (dentro del 3%)
    if tp and price > 0:
        pct_to_tp = (tp - price) / price
        if 0 <= pct_to_tp < TP_PROXIMITY_PCT:
            signals.append((SIGNAL_TP_PROXIMITY, f"precio ${price:.2f} a {pct_to_tp*100:.1f}% del TP ${tp}"))

    # 2. Precio bajo BEP
    if bep and price < bep:
        pct_under = (bep - price) / bep * 100
        signals.append((SIGNAL_BELOW_BEP, f"${price:.2f} bajo BEP ${bep:.2f} ({pct_under:.1f}% abajo)"))

    # 3. RSI bajista (< 40 y cayendo)
    if rsi < 40 and rsi < rsi_prev:
        signals.append((SIGNAL_RSI_BEARISH, f"RSI {rsi:.1f} y cayendo (antes {rsi_prev:.1f})"))

    # 4. MACD cruce bajista (cruzó hoy)
    if macd_prev >= sig_prev and macd_now < sig_now:
        signals.append((SIGNAL_MACD_CROSS, f"MACD {macd_now:.3f} cruzó bajo señal {sig_now:.3f}"))

    # 5. Precio bajo EMA20 y EMA20 cayendo
    if price < ema20 and ema20 < ema20_prev:
        signals.append((SIGNAL_BELOW_EMA20, f"${price:.2f} bajo EMA20 ${ema20:.2f} (EMA cayendo)"))

    # 6. Volumen bajista (2x+ promedio en día negativo). last_chg en %, no en
    # $ — un -6.85 aquí se leía como -6.85% cuando eran $6.85 (visto en vivo
    # con AMD el 15/07: el pipeline reportaba -6.85 siendo en realidad -1.26%).
    if last_chg_pct < 0 and avg_vol > 0 and last_vol > avg_vol * 2:
        signals.append((SIGNAL_VOLUME_SPIKE_DN, f"volumen {last_vol/avg_vol:.1f}x promedio en día {last_chg_pct:+.1f}%"))

    return signals


def _build_telegram_message(alerts: list) -> str:
    if not alerts:
        return ""

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"WATCHDOG CARTERA — {now}", ""]

    for ticker, broker, signals in alerts:
        broker_label = broker.replace("broker_", "B")
        has_tp = any(n == SIGNAL_TP_PROXIMITY for n, _ in signals)
        risk_signals = [(n, d) for n, d in signals if n != SIGNAL_TP_PROXIMITY]
        is_critical = any(n in (SIGNAL_NO_STOP, SIGNAL_EARNINGS_SOON) for n, _ in signals)
        if has_tp and not risk_signals:
            severity = "OBJETIVO"
        elif is_critical or len(risk_signals) >= 2:
            severity = "ALERTA"
        else:
            severity = "AVISO"
        lines.append(f"{severity} {ticker} ({broker_label}):")
        for name, detail in signals:
            prefix = "  +" if name == SIGNAL_TP_PROXIMITY else "  •"
            lines.append(f"{prefix} {name}: {detail}")
        lines.append("")

    lines.append("Revisar posiciones antes del cierre.")
    return "\n".join(lines)


def _send_telegram(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
            requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk},
                         timeout=10)
    except Exception as e:
        logger.warning(f"Telegram send error: {e}")


def run_watchdog(notify: bool = True) -> list:
    """
    Analiza todas las posiciones abiertas y devuelve lista de alertas.
    Si notify=True y hay alertas, envía mensaje por Telegram.
    """
    portfolio = _load_portfolio()
    if not portfolio:
        return []

    positions = portfolio.get("acciones", []) + portfolio.get("etfs", [])
    open_positions = [p for p in positions if p.get("cantidad")]

    logger.info(f"Watchdog: analizando {len(open_positions)} posiciones abiertas")

    alerts = []
    for pos in open_positions:
        ticker = pos.get("ticker", "?")
        broker = pos.get("broker", "?")
        try:
            signals = _analyze_position(pos)
            if signals:
                alerts.append((ticker, broker, signals))
                logger.info(f"{ticker}: {len(signals)} señal(es) detectada(s)")
            else:
                logger.info(f"{ticker}: OK")
        except Exception as e:
            logger.warning(f"{ticker}: error en análisis — {e}")

    if alerts and notify:
        msg = _build_telegram_message(alerts)
        _send_telegram(msg)
        logger.info(f"Watchdog: {len(alerts)} alertas enviadas por Telegram")
    elif not alerts:
        logger.info("Watchdog: todas las posiciones OK, sin alertas")

    return alerts
