"""
TradingView Webhook Server
--------------------------
Recibe alertas de TradingView y lanza el análisis de Claude automáticamente.

Uso:
    python webhook_server.py

Requiere ngrok para exponer el puerto públicamente:
    ngrok http 5000
    --> Copiar la URL https://xxxx.ngrok-free.app/webhook a TradingView

Formato del mensaje de alerta en TradingView (Pine Script):
    {"ticker": "{{ticker}}", "action": "BUY", "price": {{close}}, "timeframe": "{{interval}}"}
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, request, jsonify

from config import (
    WEBHOOK_PORT, WEBHOOK_SECRET, CONTEXT_DIR,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, LOGS_DIR,
)
from utils.logger import get_logger
from utils import telegram_notifier

app = Flask(__name__)
logger = get_logger("WebhookServer", LOGS_DIR)

_active_analyses: set = set()
_lock = threading.Lock()


# ──────────────────────────────────────────────
# Portfolio alert endpoint
# ──────────────────────────────────────────────

_ACTION_LABELS = {
    "SL_HIT":     "STOP LOSS ALCANZADO",
    "TP1_HIT":    "TARGET 1 ALCANZADO",
    "TP2_HIT":    "TARGET 2 ALCANZADO",
    "PRICE_ALERT": "ALERTA DE PRECIO",
}

_ACTION_ADVICE = {
    "SL_HIT":  "Revisar cierre inmediato de la posicion.",
    "TP1_HIT": "Considera cierre parcial o mover stop a BEP.",
    "TP2_HIT": "Considera cierre total del trade.",
}


@app.route("/alert/portfolio", methods=["POST"])
def portfolio_alert():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    if WEBHOOK_SECRET:
        token = request.headers.get("X-Webhook-Secret") or data.get("secret", "")
        if token != WEBHOOK_SECRET:
            logger.warning("Portfolio alert: secret invalido")
            return jsonify({"error": "Unauthorized"}), 401

    ticker = data.get("ticker", "").upper().strip()
    action = data.get("action", "PRICE_ALERT").upper()
    price  = float(data.get("price", 0))

    if not ticker:
        return jsonify({"error": "ticker requerido"}), 400

    logger.info(f"Portfolio alert: {ticker} | {action} | ${price:.2f}")

    position = _find_position(ticker)
    msg = _format_portfolio_alert(ticker, action, price, position)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        telegram_notifier.send_text(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)

    return jsonify({"status": "alert_sent", "ticker": ticker, "action": action}), 200


def _find_position(ticker: str) -> dict | None:
    try:
        path = Path(CONTEXT_DIR) / "portfolio.json"
        portfolio = json.loads(path.read_text(encoding="utf-8"))
        for pos in portfolio.get("acciones", []) + portfolio.get("etfs", []):
            if pos.get("ticker") == ticker:
                return pos
    except Exception as e:
        logger.error(f"Error leyendo portfolio: {e}")
    return None


def _format_portfolio_alert(ticker: str, action: str, price: float, pos: dict | None) -> str:
    label = _ACTION_LABELS.get(action, action)
    lines = [
        f"{'='*35}",
        f"{label} — {ticker}",
        f"Precio: ${price:.2f}",
    ]

    if pos:
        qty   = pos.get("cantidad", 0)
        bep   = pos.get("bep_usd") or pos.get("bep_eur") or 0
        cur   = pos.get("moneda", "USD")
        sym   = "€" if cur == "EUR" else "$"
        sl    = pos.get("stop_loss")
        tp1   = pos.get("take_profit")
        tp2   = pos.get("take_profit_2")

        pl_share = price - bep
        pl_total = pl_share * qty
        pl_pct   = (pl_share / bep * 100) if bep else 0

        lines += [
            f"Posicion: {qty} acc | BEP: {sym}{bep:.2f}",
            f"P&L: {sym}{pl_total:+.2f} ({pl_pct:+.1f}%)",
        ]
        if sl:
            lines.append(f"Stop configurado: {sym}{sl:.2f}")
        if tp1:
            tp_str = f"TP1: {sym}{tp1:.2f}"
            if tp2:
                tp_str += f" | TP2: {sym}{tp2:.2f}"
            lines.append(tp_str)

    advice = _ACTION_ADVICE.get(action)
    if advice:
        lines += ["", f">> {advice}"]

    lines.append("=" * 35)
    return "\n".join(lines)


@app.route("/alerts/config", methods=["GET"])
def alerts_config():
    """Returns ready-to-use TradingView alert JSON strings for every open position."""
    try:
        path = Path(CONTEXT_DIR) / "portfolio.json"
        portfolio = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return jsonify({"error": f"No se pudo leer portfolio: {e}"}), 500

    secret_field = f', "secret": "{WEBHOOK_SECRET}"' if WEBHOOK_SECRET else ""
    base_url = request.host_url.rstrip("/")
    endpoint = f"{base_url}/alert/portfolio"

    alerts = []
    for pos in portfolio.get("acciones", []) + portfolio.get("etfs", []):
        ticker = pos.get("ticker")
        sl  = pos.get("stop_loss")
        tp1 = pos.get("take_profit")
        tp2 = pos.get("take_profit_2")
        bep = pos.get("bep_usd") or pos.get("bep_eur", 0)

        entry = {"ticker": ticker, "broker": pos.get("broker"), "bep": bep, "alerts": []}

        if sl:
            entry["alerts"].append({
                "condition": f"{ticker} crosses below {sl}",
                "action": "SL_HIT",
                "json_body": f'{{"ticker": "{ticker}", "action": "SL_HIT", "price": {{{{close}}}}{secret_field}}}',
                "webhook_url": endpoint,
            })
        if tp1:
            entry["alerts"].append({
                "condition": f"{ticker} crosses above {tp1}",
                "action": "TP1_HIT",
                "json_body": f'{{"ticker": "{ticker}", "action": "TP1_HIT", "price": {{{{close}}}}{secret_field}}}',
                "webhook_url": endpoint,
            })
        if tp2:
            entry["alerts"].append({
                "condition": f"{ticker} crosses above {tp2}",
                "action": "TP2_HIT",
                "json_body": f'{{"ticker": "{ticker}", "action": "TP2_HIT", "price": {{{{close}}}}{secret_field}}}',
                "webhook_url": endpoint,
            })

        if entry["alerts"]:
            alerts.append(entry)

    return jsonify({"endpoint": endpoint, "positions": alerts}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "active_analyses": list(_active_analyses),
    })


@app.route("/webhook", methods=["POST"])
def tradingview_webhook():
    data = request.get_json(silent=True)
    if not data:
        logger.warning("Webhook: payload vacío o no es JSON")
        return jsonify({"error": "Invalid JSON"}), 400

    if WEBHOOK_SECRET:
        token = (
            request.headers.get("X-Webhook-Secret")
            or data.get("secret", "")
        )
        if token != WEBHOOK_SECRET:
            logger.warning("Webhook: secret inválido")
            return jsonify({"error": "Unauthorized"}), 401

    ticker = data.get("ticker", "").upper().strip().replace(".NS", "").replace(".US", "")
    if not ticker:
        return jsonify({"error": "campo 'ticker' requerido"}), 400

    action = data.get("action", "ALERT").upper()
    price = float(data.get("price", 0))
    timeframe = data.get("timeframe", "D")

    logger.info(f"TradingView alert recibida: {ticker} | {action} | ${price:.2f} | TF:{timeframe}")

    with _lock:
        if ticker in _active_analyses:
            logger.info(f"Análisis de {ticker} ya en curso, ignorando duplicado")
            return jsonify({"status": "already_running", "ticker": ticker}), 200
        _active_analyses.add(ticker)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        telegram_notifier.send_text(
            TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
            f"TRADINGVIEW ALERT: {ticker}\n"
            f"Accion: {action} | Precio: ${price:.2f} | TF: {timeframe}\n"
            f"Iniciando analisis Claude... (aprox. 5 min)",
        )

    thread = threading.Thread(
        target=_run_analysis,
        args=(ticker, action, price),
        daemon=True,
    )
    thread.start()

    return jsonify({"status": "analysis_started", "ticker": ticker}), 200


def _run_analysis(ticker: str, action: str, price: float):
    try:
        from agents.orchestrator import TradingOrchestrator
        orch = TradingOrchestrator(
            override_tickers=[ticker],
            session="webhook",
        )
        report = orch.run_daily_pipeline()

        if report and report.candidates:
            fc = report.candidates[0]
            risk = fc.risk_data
            msg = (
                f"ANALISIS COMPLETADO: {ticker}\n"
                f"Señal: {action} | Precio alerta: ${price:.2f}\n"
                f"Recomendacion: {fc.recommendation} (score: {fc.composite_score:.1f})\n"
            )
            if risk:
                msg += (
                    f"Entrada: ${risk.entry_price:.2f}\n"
                    f"Stop:    ${risk.stop_loss:.2f}\n"
                    f"TP1:     ${risk.target_1:.2f} (R:R {risk.rr_ratio_1:.1f}:1)\n"
                    f"TP2:     ${risk.target_2:.2f} (R:R {risk.rr_ratio_2:.1f}:1)\n"
                )
            if fc.summary:
                msg += f"\n{fc.summary}"
        else:
            msg = (
                f"ANALISIS {ticker}: sin setup valido en este momento.\n"
                f"Señal recibida: {action} @ ${price:.2f}"
            )

        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            telegram_notifier.send_text(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)

        logger.info(f"Análisis webhook completado: {ticker}")

    except Exception as e:
        logger.error(f"Error en análisis webhook {ticker}: {e}")
        if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
            telegram_notifier.send_text(
                TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
                f"ERROR analizando {ticker}: {str(e)[:120]}",
            )
    finally:
        with _lock:
            _active_analyses.discard(ticker)


if __name__ == "__main__":
    logger.info(f"=== Webhook server iniciando en puerto {WEBHOOK_PORT} ===")
    logger.info("Endpoints:")
    logger.info(f"  GET  http://localhost:{WEBHOOK_PORT}/health")
    logger.info(f"  GET  http://localhost:{WEBHOOK_PORT}/alerts/config  <- alertas TV listas para copiar")
    logger.info(f"  POST http://localhost:{WEBHOOK_PORT}/webhook         <- analisis completo")
    logger.info(f"  POST http://localhost:{WEBHOOK_PORT}/alert/portfolio <- SL/TP en tiempo real")
    logger.info("")
    logger.info("Para exponer a internet: ngrok http 5000")
    logger.info("URL base TradingView: https://XXXX.ngrok-free.app")
    app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)
