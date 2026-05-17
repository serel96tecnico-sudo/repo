import requests
from utils.logger import get_logger
from config import LOGS_DIR


logger = get_logger("Telegram", LOGS_DIR)

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


def send_report(token: str, chat_id: str, report) -> bool:
    """Send a DailyReport summary to Telegram. Returns True on success."""
    if not token or not chat_id:
        return False
    try:
        text = _format_message(report)
        return _send_message(token, chat_id, text)
    except Exception as e:
        logger.error(f"Telegram notification failed: {e}")
        return False


def send_text(token: str, chat_id: str, text: str) -> bool:
    if not token or not chat_id:
        return False
    try:
        return _send_message(token, chat_id, text)
    except Exception as e:
        logger.error(f"Telegram send_text failed: {e}")
        return False


def _send_message(token: str, chat_id: str, text: str) -> bool:
    url = TELEGRAM_API.format(token=token, method="sendMessage")
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        resp = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=15, verify=False)
        if not resp.ok:
            logger.error(f"Telegram API error {resp.status_code}: {resp.text}")
            return False
    return True


def _format_message(report) -> str:
    market = report.market_conditions
    lines = [
        f"INFORME SWING TRADING — {report.report_date}",
        f"Mercado: {market.spy_trend} | VIX: {market.vix_level} | {market.regime}",
        f"Escaneados: {report.total_scanned} tickers | Tiempo: {report.generation_time_seconds}s",
        "",
    ]

    if not report.candidates:
        lines.append("Hoy no hay candidatos que cumplan los criterios.")
        return "\n".join(lines)

    for fc in report.candidates:
        risk = fc.risk_data
        ta = fc.ta_data
        direction = ta.direction if ta else "long"

        dir_label = "CORTO | Broker 2" if direction == "short" else "LARGO | Broker 1 o 2"

        lines.append(f"{'='*35}")
        lines.append(f"#{fc.rank} {fc.ticker} — {fc.recommendation} ({fc.composite_score:.1f})")
        lines.append(dir_label)

        if risk:
            stop_pct = (risk.stop_loss - risk.entry_price) / risk.entry_price * 100
            t1_pct = (risk.target_1 - risk.entry_price) / risk.entry_price * 100
            lines.append(f"Entrada: ${risk.entry_zone_low:.2f}-${risk.entry_zone_high:.2f}")
            lines.append(f"Stop:    ${risk.stop_loss:.2f} ({stop_pct:+.1f}%)")
            lines.append(f"T1:      ${risk.target_1:.2f} ({t1_pct:+.1f}%) R:R 1:{risk.rr_ratio_1:.1f}")
            lines.append(f"T2:      ${risk.target_2:.2f} | {risk.position_size_shares} acc | Pérd. máx: ${risk.max_loss_dollars:.0f}")

        if fc.summary:
            lines.append(fc.summary)

        lines.append("")

    return "\n".join(lines)
