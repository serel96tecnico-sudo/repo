"""
Price Watcher — vigila los niveles armados en contex/price_alerts.json y avisa
por Telegram cuando el precio toca uno.

Es el consumidor de arm_alerts.py: aquel arma la lista (que nivel vigilar por
cada WATCH del pipeline); este la lee, consulta precios en bucle durante la
sesion y dispara. NO ejecuta ordenes.

En el disparo recalcula el stop por ATR desde el precio real de disparo — el
stop guardado en el armado era para otra entrada y a este precio no sirve. El
tamano de posicion y la revalidacion de reglas de cartera (R1/R2/R6/R7) NO se
automatizan aqui: el aviso lo deja explicito para confirmarlo antes de entrar.

Uso:
    python scripts/price_watcher.py                 # bucle continuo (sesion US)
    python scripts/price_watcher.py --once          # una pasada y salir
    python scripts/price_watcher.py --interval 300  # cada 5 min (por defecto)
    python scripts/price_watcher.py --once --dry-run # no envia Telegram, imprime
    python scripts/price_watcher.py --ignore-hours  # no exige mercado abierto
"""
import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# La consola de Windows es cp1252 y revienta al imprimir emoji; Telegram (UTF-8
# por HTTP) no. Forzamos stdout a UTF-8 para que el print local no falle.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from config import (
    CONTEXT_DIR, LOGS_DIR, ATR_STOP_MULTIPLIER,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, US_MARKET_HOLIDAYS_2026,
)
from data.market_data import MarketDataFetcher
from utils.logger import get_logger
from utils import telegram_notifier

logger = get_logger("PriceWatcher", LOGS_DIR)

ALERTS_FILE = CONTEXT_DIR / "price_alerts.json"
DEFAULT_INTERVAL = 300  # 5 min: un pullback swing es evento de minutos, no de ticks


def _market_open(now_utc: datetime = None) -> bool:
    """True si el mercado US esta en sesion regular (9:30-16:00 ET, L-V, no festivo)."""
    if ZoneInfo is None:
        # Sin zoneinfo no arriesgamos a fallar cerrado: dejamos vigilar.
        return True
    et = (now_utc or datetime.now(ZoneInfo("UTC"))).astimezone(ZoneInfo("America/New_York"))
    if et.weekday() >= 5:
        return False
    if et.strftime("%Y-%m-%d") in US_MARKET_HOLIDAYS_2026:
        return False
    minutes = et.hour * 60 + et.minute
    return 9 * 60 + 30 <= minutes <= 16 * 60


def _load_alerts() -> dict:
    if not ALERTS_FILE.exists():
        return {}
    try:
        return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"No se pudo leer {ALERTS_FILE}: {e}")
        return {}


def _save_alerts(data: dict) -> None:
    tmp = ALERTS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ALERTS_FILE)


def _is_live(alert: dict, today_iso: str) -> bool:
    """Una alerta se vigila si no ha disparado y no ha caducado."""
    if alert.get("fired"):
        return False
    if today_iso > alert.get("expires", today_iso):
        return False
    return True


def _triggered(alert: dict, price: float) -> bool:
    """side 'below': esperamos un pullback bajando al nivel -> dispara si el
    precio entra en [nivel - tol, ...] por arriba, i.e. price <= nivel + tol.
    side 'above': ruptura al alza -> dispara si price >= nivel - tol."""
    level = alert["level"]
    tol = alert.get("tolerance", 0) or 0
    if alert["side"] == "below":
        return price <= level + tol
    return price >= level - tol


def _recalc_stop(alert: dict, price: float):
    """Stop por ATR desde el precio de disparo. Determinista, sin Claude. El
    tamano de posicion (R3/VIX) y R1/R2/R6/R7 NO se calculan aqui."""
    atr = (alert.get("context") or {}).get("atr")
    if not atr:
        return None, None
    if alert["direction"] == "short":
        stop = price + ATR_STOP_MULTIPLIER * atr
    else:
        stop = price - ATR_STOP_MULTIPLIER * atr
    return round(stop, 2), round(abs(price - stop), 2)


def _format_alert(alert: dict, price: float) -> str:
    c = alert.get("context") or {}
    d = "LARGO" if alert["direction"] != "short" else "CORTO"
    arrow = "▼ pullback" if alert["side"] == "below" else "▲ ruptura"
    stop, risk = _recalc_stop(alert, price)

    lines = [
        f"🔔 ALERTA {alert['ticker']} — {d}  ({arrow})",
        f"Precio {price:.2f} tocó nivel {alert['level']:.2f} ({alert.get('source','?')})",
        f"Trigger original: {alert.get('entry_trigger') or 'n/d'}",
        f"Armada {alert.get('armed','?')} · score {c.get('score','?')} · ATR {c.get('atr','?')}",
        "",
    ]
    if stop is not None:
        lines.append(f"Stop sugerido (ATR×{ATR_STOP_MULTIPLIER} desde disparo): {stop:.2f}  ·  riesgo/acción {risk:.2f}")
    if c.get("t1_hint"):
        lines.append(f"T1 orientativo (del armado): {c['t1_hint']}")
    lines += [
        "",
        "⚠ Falta antes de entrar: tamaño (R3/VIX) y validar R1/R2/R6/R7",
        "contra la cartera actual. El stop guardado en el armado NO sirve a",
        "este precio; usa el recalculado de arriba.",
    ]
    return "\n".join(lines)


def check_once(fetcher: MarketDataFetcher, dry_run: bool = False) -> int:
    """Una pasada: consulta precios, dispara los que toquen nivel. Devuelve nº disparos."""
    data = _load_alerts()
    alerts = data.get("alerts") or []
    today_iso = date.today().isoformat()
    live = [a for a in alerts if _is_live(a, today_iso)]
    if not live:
        logger.info("Sin alertas vivas que vigilar.")
        return 0

    tickers = sorted({a["ticker"] for a in live})
    quotes = fetcher.fetch_batch_quotes(tickers)
    fired = 0

    for alert in live:
        q = quotes.get(alert["ticker"])
        if not q or "price" not in q:
            logger.warning(f"{alert['ticker']}: sin precio, se salta esta pasada")
            continue
        price = q["price"]
        if not _triggered(alert, price):
            continue

        msg = _format_alert(alert, price)
        logger.info(f"DISPARO {alert['ticker']} @ {price:.2f} (nivel {alert['level']})")
        print("\n" + msg + "\n")

        sent = False
        if not dry_run:
            sent = telegram_notifier.send_text(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
            if not sent:
                logger.warning(f"{alert['ticker']}: Telegram no enviado (¿tokens?). Alerta NO marcada como disparada.")

        # Solo marcamos 'fired' si el aviso llego a salir (o en dry-run). Asi un
        # fallo de Telegram no silencia la alerta: se reintenta la pasada siguiente.
        if dry_run or sent:
            alert["fired"] = datetime.now().isoformat(timespec="seconds")
            alert["fired_price"] = round(price, 4)
            fired += 1

    if fired and not dry_run:
        _save_alerts(data)
    return fired


def main():
    ap = argparse.ArgumentParser(description="Vigila los niveles armados y avisa por Telegram")
    ap.add_argument("--once", action="store_true", help="una sola pasada y salir")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="segundos entre pasadas")
    ap.add_argument("--dry-run", action="store_true", help="no envia Telegram, solo imprime")
    ap.add_argument("--ignore-hours", action="store_true", help="vigila aunque el mercado este cerrado")
    args = ap.parse_args()

    fetcher = MarketDataFetcher(context_dir=CONTEXT_DIR)

    if args.once:
        n = check_once(fetcher, dry_run=args.dry_run)
        logger.info(f"Pasada unica: {n} disparo(s).")
        return

    logger.info(f"Price watcher iniciado (intervalo {args.interval}s).")
    while True:
        try:
            if args.ignore_hours or _market_open():
                check_once(fetcher, dry_run=args.dry_run)
            else:
                logger.debug("Mercado cerrado, en espera.")
        except Exception as e:
            logger.error(f"Error en pasada de vigilancia: {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
