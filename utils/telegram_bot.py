import threading
import time
import requests
from pathlib import Path

from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    LOGS_DIR, CONTEXT_DIR, OUTPUT_DIR,
)
from utils.logger import get_logger
from utils.context_manager import ContextManager

logger = get_logger("TelegramBot", LOGS_DIR)

_API = "https://api.telegram.org/bot{token}/{method}"


def _api(method: str, **kwargs) -> dict:
    url = _API.format(token=TELEGRAM_BOT_TOKEN, method=method)
    try:
        resp = requests.post(url, json=kwargs, timeout=35, verify=False)
        return resp.json()
    except Exception as e:
        logger.error(f"Telegram API error ({method}): {e}")
        return {}


def _send(text: str) -> None:
    for chunk in [text[i:i+4096] for i in range(0, len(text), 4096)]:
        _api("sendMessage", chat_id=TELEGRAM_CHAT_ID, text=chunk)


# ── Comandos ──────────────────────────────────────────────

def _cartera() -> str:
    ctx = ContextManager(CONTEXT_DIR, OUTPUT_DIR)
    pf = ctx.load_portfolio()
    if not pf:
        return "No hay datos de cartera."

    b1 = pf.get("brokers", {}).get("broker_1", {})
    b2 = pf.get("brokers", {}).get("broker_2", {})
    b3 = pf.get("brokers", {}).get("broker_3", {})

    lines = [f"CARTERA — {pf.get('last_updated', '?')}",
             "",
             f"B1 DeGiro: €{b1.get('cuenta_completa_eur', 0):.2f}",
             f"  Margen libre: €{b1.get('margen_libre_eur', 0):.2f}",
             f"  Dia: {b1.get('dia_bp_eur', 0):+.2f}€  |  Total B/P: {b1.get('total_bp_eur', 0):+.2f}€"]

    b1_pos = [p for p in pf.get("acciones", []) + pf.get("etfs", [])
              if p["broker"] == "broker_1" and p.get("cantidad", 0)]
    for p in b1_pos:
        gp = p.get("gp_eur", 0) or 0
        pct = p.get("gp_pct", 0) or 0
        precio = (p.get("precio_actual_usd") or p.get("bep_usd")
                  or p.get("precio_actual_eur") or p.get("bep_eur") or 0)
        moneda = "€" if p.get("moneda") == "EUR" else "$"
        lines.append(f"  {p['ticker']}: {moneda}{precio:.2f}  hoy {gp:+.2f}€ ({pct:+.1f}%)")

    lines += ["",
              f"B2 ColmexPro: ${b2.get('balance_usd', 0):.2f}",
              f"  Gross P/L: ${b2.get('open_gross_pl_usd', 0):+.2f}  |  Cash libre: ${b2.get('margin_available_usd', 0):.2f}"]

    b2_pos = [p for p in pf.get("acciones", [])
              if p["broker"] == "broker_2" and p.get("cantidad", 0)]
    for p in b2_pos:
        net = p.get("net_pl_usd", 0) or 0
        precio = (p.get("precio_actual_usd") or p.get("bep_usd") or 0)
        lines.append(f"  {p['ticker']}: ${precio:.2f}  net {net:+.2f}$")

    lines += ["",
              f"B3 FPMTrading: ${b3.get('balance_usd', 0):.2f}  |  P/L: ${b3.get('open_profit_usd', 0):+.2f}"]

    return "\n".join(lines)


def _precio(ticker: str) -> str:
    try:
        from data.market_data import MarketDataFetcher
        fetcher = MarketDataFetcher(context_dir=CONTEXT_DIR)
        quotes = fetcher.refresh_daily_quotes([ticker.upper()])
        q = quotes.get(ticker.upper(), {})
        if q and q.get("price"):
            chg = q.get("change_pct", 0) or 0
            return f"{ticker.upper()}: ${q['price']:.2f}  ({chg:+.2f}%)"
        return f"No se pudo obtener precio de {ticker.upper()}."
    except Exception as e:
        return f"Error al obtener precio: {e}"


def _cerrar(args: list) -> str:
    if len(args) < 2:
        return "Uso: /cerrar TICKER precio [notas]"
    ticker = args[0].upper()
    try:
        exit_price = float(args[1])
    except ValueError:
        return "Precio no valido."
    notas = " ".join(args[2:]) if len(args) > 2 else ""

    ctx = ContextManager(CONTEXT_DIR, OUTPUT_DIR)
    pf = ctx.load_portfolio()
    pos = next((p for p in pf.get("acciones", []) if p["ticker"].upper() == ticker), None)
    if not pos:
        return f"{ticker} no encontrado en cartera."

    entry = pos.get("bep_usd") or 0
    qty = pos.get("cantidad", 0)
    direccion = pos.get("direccion", "long")
    gross = (exit_price - entry) * qty if direccion == "long" else (entry - exit_price) * qty
    commission = 5.00 if pos.get("broker") == "broker_2" else 0
    net = gross - commission

    result = "GANANCIA" if net > 0 else "PERDIDA"
    lines = [
        f"Cierre {ticker} @${exit_price:.2f}",
        f"Entrada BEP: ${entry:.2f} | {qty} acc | {direccion.upper()}",
        f"Gross P/L: ${gross:+.2f}",
        f"Comision: ${commission:.2f}",
        f"Net P/L:   ${net:+.2f}  [{result}]",
        "Recuerda actualizar portfolio.json.",
    ]
    if notas:
        lines.append(f"Notas: {notas}")
    return "\n".join(lines)


def _pipeline_async(session: str) -> None:
    def run():
        _send(f"Lanzando pipeline {session}... (~5 min)")
        try:
            from agents.orchestrator import TradingOrchestrator
            orch = TradingOrchestrator(session=session)
            report = orch.run_daily_pipeline()
            if report:
                top = "\n".join(
                    f"#{fc.rank} {fc.ticker} — {fc.recommendation} ({fc.composite_score:.1f})"
                    for fc in report.candidates[:4]
                )
                _send(f"Pipeline {session} completado.\n{top}")
            else:
                _send("Pipeline sin resultados (mercado cerrado o error).")
        except Exception as e:
            _send(f"Error en pipeline: {e}")

    threading.Thread(target=run, daemon=True).start()


def _watchdog_async() -> None:
    def run():
        _send("Analizando posiciones abiertas... (~1 min)")
        try:
            from agents.portfolio_watchdog import run_watchdog, SIGNAL_TP_PROXIMITY
            alerts = run_watchdog(notify=False)
            if not alerts:
                _send("Todas las posiciones OK — sin señales de alerta.")
                return
            import datetime as dt
            lines = [f"WATCHDOG — {dt.datetime.now().strftime('%H:%M')}", ""]
            for ticker, broker, signals in alerts:
                broker_label = broker.replace("broker_", "B")
                has_tp = any(n == SIGNAL_TP_PROXIMITY for n, _ in signals)
                risk = [(n, d) for n, d in signals if n != SIGNAL_TP_PROXIMITY]
                severity = "OBJETIVO" if has_tp and not risk else ("ALERTA" if len(risk) >= 2 else "AVISO")
                lines.append(f"{severity} {ticker} ({broker_label}):")
                for name, detail in signals:
                    prefix = "  +" if name == SIGNAL_TP_PROXIMITY else "  •"
                    lines.append(f"{prefix} {name}: {detail}")
                lines.append("")
            _send("\n".join(lines))
        except Exception as e:
            _send(f"Error en watchdog: {e}")

    threading.Thread(target=run, daemon=True).start()


def _scan_async() -> None:
    def run():
        _send("Escaneando watchlist en busca de entradas... (~3 min)")
        try:
            from agents.entry_scanner import run_entry_scanner
            results = run_entry_scanner(notify=False)
            if not results:
                _send("Sin señales de entrada en este momento.")
                return
            import datetime as dt
            lines = [f"ENTRY SCAN — {dt.datetime.now().strftime('%H:%M')}", ""]
            for r in results:
                if r["long_signals"]:
                    lines.append(f"LONG {r['ticker']} @${r['price']:.2f} RSI {r['rsi']} Vol {r['vol_ratio']}x")
                    for name, detail in r["long_signals"]:
                        lines.append(f"  + {name}: {detail}")
                if r["short_signals"]:
                    lines.append(f"SHORT {r['ticker']} @${r['price']:.2f} RSI {r['rsi']} Vol {r['vol_ratio']}x")
                    for name, detail in r["short_signals"]:
                        lines.append(f"  - {name}: {detail}")
                lines.append("")
            _send("\n".join(lines))
        except Exception as e:
            _send(f"Error en entry scan: {e}")

    threading.Thread(target=run, daemon=True).start()


def _ayuda() -> str:
    return (
        "Comandos disponibles:\n"
        "\n"
        "/cartera        — posiciones actuales (todos los brokers)\n"
        "/precio TICKER  — precio actual de un ticker\n"
        "/watchdog       — analizar posiciones: SL/TP cercanos, señales de giro\n"
        "/scan           — escanear watchlist en busca de entradas\n"
        "/pipeline       — lanza analisis sesion manana\n"
        "/pipeline tarde — lanza analisis sesion tarde\n"
        "/cerrar TICKER precio [notas] — calcula P/L de cierre\n"
        "/ayuda          — este mensaje"
    )


# ── Dispatcher ────────────────────────────────────────────

def _handle(message: dict) -> None:
    chat_id = str(message.get("chat", {}).get("id", ""))
    if chat_id != str(TELEGRAM_CHAT_ID):
        return

    text = message.get("text", "").strip()
    if not text.startswith("/"):
        return

    parts = text.split()
    cmd = parts[0].lstrip("/").lower().split("@")[0]
    args = parts[1:]

    if cmd in ("cartera", "c", "portfolio"):
        _send(_cartera())
    elif cmd == "precio" and args:
        _send(_precio(args[0]))
    elif cmd in ("watchdog", "wdog", "posiciones"):
        _watchdog_async()
    elif cmd in ("scan", "escanear", "entradas"):
        _scan_async()
    elif cmd == "pipeline":
        session = "evening" if args and args[0] in ("tarde", "evening") else "morning"
        _pipeline_async(session)
    elif cmd == "cerrar":
        _send(_cerrar(args))
    elif cmd in ("ayuda", "help", "start"):
        _send(_ayuda())
    else:
        _send(f"Comando desconocido: /{cmd}\nEscribe /ayuda para ver los disponibles.")


# ── Loop principal ─────────────────────────────────────────

def run_bot() -> None:
    logger.info("Telegram bot iniciado (long polling)")
    _send("Bot de trading activo. /ayuda para ver comandos.")

    offset = 0
    while True:
        try:
            result = _api("getUpdates", offset=offset, timeout=30)
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                msg = update.get("message") or update.get("edited_message")
                if msg:
                    _handle(msg)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)
