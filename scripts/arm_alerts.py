"""
Arm Alerts — arma alertas de precio sobre los candidatos WATCH del informe.

Lee el informe del pipeline (output/report_YYYY-MM-DD[_evening].json), selecciona
los candidatos degradados a WATCH con score suficiente y resuelve, para cada uno,
el nivel de precio que hay que vigilar segun su entry_trigger.

NO ejecuta ordenes ni vigila precios: solo arma la lista. La vigilancia y el
recalculo de riesgo en el disparo van aparte.

Uso:
    python scripts/arm_alerts.py                 # previsualiza (no escribe)
    python scripts/arm_alerts.py --write         # escribe contex/price_alerts.json
    python scripts/arm_alerts.py --date 2026-07-17 --min-score 6.5
"""
import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import CONTEXT_DIR, OUTPUT_DIR
from utils.risk_policy import recent_loss_cooldown

ALERTS_FILE = CONTEXT_DIR / "price_alerts.json"

# Score minimo para armar. Un WATCH de 4.0 no es un setup en espera, es un
# descarte: no queremos vigilar precios que nunca vamos a operar.
DEFAULT_MIN_SCORE = 6.0

# Vida de la alerta en sesiones. Un pullback que no llega en una semana ya no
# es el mismo setup: los indicadores que lo definian se han movido.
EXPIRY_TRADING_DAYS = 5

# Zona de disparo alrededor del nivel, en ATRs. Con tolerancia 0 nos perdemos
# el aviso si el minimo del dia queda 7 centimos por encima del nivel.
TOLERANCE_ATR = 0.25

# Distancia minima al nivel para que armar tenga sentido, en ATRs. Si el precio
# ya esta pegado a su EMA9, no hay pullback pendiente que vigilar: la alerta
# saltaria en el primer tick de la sesion siguiente sin informar de nada. Esos
# casos no se descartan, se separan como "ya en zona" — son decision de hoy.
MIN_DISTANCE_ATR = 0.5

# Informes ausentes consecutivos que una alerta tolera antes de retirarse. El
# pipeline corre 2 veces al dia (manana/tarde) y un candidato bueno puede no
# entrar en el corte de un run concreto sin que el setup haya muerto (a ACIW le
# paso el 20/07). Con 2, sobrevive a un hueco aislado; a 3 ausencias se retira.
MISSING_GRACE = 2

# Un nivel mas lejos que esto es ruido (indicador desfasado o trigger absurdo).
MAX_DISTANCE_PCT = 0.25


def _load_report(day: str = None) -> tuple:
    """Devuelve (datos, ruta) del informe pedido, o del mas reciente."""
    if day:
        candidates = sorted(OUTPUT_DIR.glob(f"report_{day}*.json"))
    else:
        candidates = sorted(OUTPUT_DIR.glob("report_*.json"))
    candidates = [p for p in candidates if not p.name.endswith("_alerts.json")]
    if not candidates:
        raise SystemExit(f"No hay informe que coincida (day={day!r}) en {OUTPUT_DIR}")
    path = max(candidates, key=lambda p: (p.stem, p.stat().st_mtime))
    return json.loads(path.read_text(encoding="utf-8")), path


def _open_tickers(portfolio: dict) -> set:
    """Tickers ya en cartera — no se arma alerta sobre lo que ya tenemos."""
    out = set()
    for bucket in ("acciones", "etfs", "crypto", "cfds"):
        for pos in portfolio.get(bucket) or []:
            if isinstance(pos, dict) and pos.get("ticker"):
                out.add(str(pos["ticker"]).upper())
    return out


def _expiry(armed: date, sessions: int = EXPIRY_TRADING_DAYS) -> date:
    """Suma sesiones saltando fines de semana (sin calendario de festivos)."""
    day, left = armed, sessions
    while left > 0:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day


def _nearest_below(levels, price):
    below = [lv for lv in levels if lv and lv < price]
    return max(below) if below else None


def _nearest_above(levels, price):
    above = [lv for lv in levels if lv and lv > price]
    return min(above) if above else None


def resolve_level(cand: dict) -> dict:
    """Traduce el entry_trigger del candidato a un nivel de precio vigilable.

    entry_trigger lo escribe Claude en texto libre ("pullback_to_ema21_with_
    volume_confirmation", "breakout_above_resistance_on_volume_surge", y en un
    tercio de los casos cadena vacia), asi que se busca por subcadenas y siempre
    hay un fallback por direccion. Devuelve dict con motivo si no se puede armar.
    """
    ta = cand.get("ta_data") or {}
    ind = ta.get("indicators") or {}
    price = cand.get("current_price") or ind.get("price")
    if not price:
        return {"skip": "sin precio actual"}

    direction = (ta.get("direction") or cand.get("scan_data", {}).get("setup_direction") or "long").lower()
    trigger = (ta.get("entry_trigger") or "").lower()
    supports = ta.get("support_levels") or []
    resistances = ta.get("resistance_levels") or []

    level = side = source = None
    note = ""

    # 1. Pullback a una media concreta — el caso mas comun en longs.
    if "pullback" in trigger or "bounce" in trigger:
        for name in ("ema9", "ema21", "ema50"):
            if name in trigger and ind.get(name):
                level, side, source = ind[name], "below", name.upper()
                break
        if level and level >= price:
            # La media citada esta POR ENCIMA del precio: no es un pullback,
            # el precio ya cayo por debajo. El setup narrado no existe ya.
            note = f"{source} ({level:.2f}) esta sobre el precio — sustituido por soporte"
            level = source = None
        if level is None:
            lv = _nearest_below(supports, price)
            if lv:
                level, side, source = lv, "below", "soporte"

    # 2. Ruptura al alza.
    elif "breakout" in trigger or "break_above" in trigger:
        lv = _nearest_above(resistances, price) or ind.get("base_high") or ind.get("high_52w")
        if lv and lv > price:
            level, side, source = lv, "above", "resistencia"

    # 3. Ruptura a la baja / rechazo — setups cortos.
    elif "breakdown" in trigger or "below_support" in trigger:
        lv = _nearest_below(supports, price)
        if lv:
            level, side, source = lv, "below", "soporte"
    elif "rejection" in trigger:
        lv = _nearest_above(resistances, price)
        if lv:
            level, side, source = lv, "above", "resistencia"

    # 4. Fallback: trigger vacio o no reconocido. Se vigila el nivel natural
    #    segun direccion — soporte inmediato en largos, resistencia en cortos.
    if level is None:
        if direction == "short":
            lv = _nearest_above(resistances, price)
            if lv:
                level, side, source = lv, "above", "resistencia (fallback)"
        else:
            lv = _nearest_below(supports, price)
            if lv:
                level, side, source = lv, "below", "soporte (fallback)"

    if level is None:
        return {"skip": f"sin nivel vigilable (trigger={trigger or 'vacio'})"}

    distance_pct = abs(level - price) / price
    if distance_pct > MAX_DISTANCE_PCT:
        return {"skip": f"nivel {level:.2f} a {distance_pct:.0%} del precio — descartado"}

    atr = ind.get("atr_14") or 0.0
    tolerance = round(atr * TOLERANCE_ATR, 2) if atr else round(price * 0.002, 2)

    # Distancia en ATRs: es la unidad correcta. Un 0.3% es mucho en KO y nada
    # en AMD; comparar contra el ATR normaliza por la volatilidad del valor.
    distance = abs(level - price)
    distance_atr = round(distance / atr, 2) if atr else None
    in_zone = bool(atr) and distance < atr * MIN_DISTANCE_ATR

    return {
        "level": round(float(level), 2),
        "side": side,
        "source": source,
        "tolerance": tolerance,
        "distance_pct": round(distance_pct * 100, 2),
        "distance_atr": distance_atr,
        "in_zone": in_zone,
        "direction": direction,
        "note": note,
    }


def build_alerts(report: dict, portfolio: dict, min_score: float) -> tuple:
    """Devuelve (alertas, ya_en_zona, descartes) a partir del informe.

    "ya en zona" no son alertas: son candidatos cuyo nivel esta tan cerca del
    precio que no queda espera que vigilar. Se listan aparte porque son una
    decision de hoy (entrar o dejar pasar), no un aviso futuro.
    """
    armed = date.today()
    held = _open_tickers(portfolio)
    # R7 — un ticker recien stopeado que se re-recomienda a los pocos dias es el
    # patron que mas pierde. No tiene sentido armarle una alerta de re-entrada en
    # la misma direccion. Reutiliza la logica de la politica de riesgo.
    cooldown = recent_loss_cooldown(portfolio)
    alerts, in_zone, skipped = [], [], []

    for cand in report.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        ticker = (cand.get("ticker") or "").upper()
        score = cand.get("composite_score") or 0.0
        rec = cand.get("recommendation") or ""

        if rec != "WATCH":
            continue
        if ticker in held:
            skipped.append((ticker, score, "ya en cartera"))
            continue
        if score < min_score:
            skipped.append((ticker, score, f"score {score:.2f} < {min_score}"))
            continue

        res = resolve_level(cand)
        if "skip" in res:
            skipped.append((ticker, score, res["skip"]))
            continue

        # R7: veto si el ticker cerro en perdida reciente en esta misma direccion
        # (direction None en el cierre => veta cualquier direccion, conservador).
        cd = cooldown.get(ticker)
        if cd and cd["direction"] in (None, res["direction"]):
            dirtxt = "cualquier dir" if cd["direction"] is None else cd["direction"]
            skipped.append((ticker, score,
                            f"R7 cooldown: perdida {cd['pl']} hace {cd['days_ago']}d ({dirtxt})"))
            continue

        ta = cand.get("ta_data") or {}
        risk = cand.get("risk_data") or {}
        entry = {
            "ticker": ticker,
            "level": res["level"],
            "side": res["side"],
            "tolerance": res["tolerance"],
            "distance_atr": res["distance_atr"],
            "direction": res["direction"],
            "source": res["source"],
            "entry_trigger": ta.get("entry_trigger") or "",
            "armed": armed.isoformat(),
            "expires": _expiry(armed).isoformat(),
            "fired": None,
            "missing": 0,
            # Contexto informativo del dia del armado. NO son parametros
            # operativos: al dispararse hay que recalcular sizing/stop y
            # revalidar R1/R2/R6/R7 contra la cartera de ese momento.
            "context": {
                "price_at_arm": cand.get("current_price"),
                "score": score,
                "atr": (ta.get("indicators") or {}).get("atr_14"),
                "stop_hint": risk.get("stop_loss"),
                "t1_hint": risk.get("target_1"),
                "subtheme": risk.get("subtheme"),
                "tier": risk.get("tier"),
                "note": res.get("note", ""),
            },
        }
        (in_zone if res["in_zone"] else alerts).append(entry)

    alerts.sort(key=lambda a: -a["context"]["score"])
    in_zone.sort(key=lambda a: -a["context"]["score"])
    return alerts, in_zone, skipped


def _load_existing() -> dict:
    if ALERTS_FILE.exists():
        try:
            return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def merge_alerts(new_alerts, existing, held, today=None, grace=MISSING_GRACE):
    """Fusiona las alertas recien armadas con las del fichero previo.

    Una alerta armada vive hasta que caduca, se llena su gracia de ausencias, o
    su ticker entra en cartera. Mientras siga apareciendo en el informe se
    re-arma (nivel fresco) pero conserva su reloj original. Devuelve
    (activas, retiradas) con retiradas = lista de (ticker, motivo).
    """
    today = today or date.today()
    today_iso = today.isoformat()
    new_by = {a["ticker"]: a for a in new_alerts}
    prev = {a["ticker"]: a for a in (existing or {}).get("alerts", [])}
    active, retired = [], []

    for tk, old in prev.items():
        if tk in held:
            retired.append((tk, "entrada abierta"))
            continue
        expired = today_iso > old.get("expires", today_iso)

        if tk in new_by:
            fresh = dict(new_by[tk])
            if expired:
                # Reaparece pero su ventana original ya vencio: reloj nuevo.
                active.append(fresh)
            else:
                # Re-armado: nivel y contexto frescos, reloj original intacto.
                fresh["armed"] = old.get("armed", fresh["armed"])
                fresh["expires"] = old.get("expires", fresh["expires"])
                fresh["fired"] = old.get("fired")
                fresh["missing"] = 0
                active.append(fresh)
        else:
            # Ausente del informe de hoy.
            if expired:
                retired.append((tk, "caducada"))
                continue
            miss = old.get("missing", 0) + 1
            if miss > grace:
                retired.append((tk, f"ausente {miss} informes"))
                continue
            old["missing"] = miss
            active.append(old)  # nivel congelado del ultimo armado

    for tk, fresh in new_by.items():
        if tk not in prev:
            active.append(dict(fresh))

    active.sort(key=lambda a: -a["context"]["score"])
    return active, retired


def _rows(entries, show_expiry=True):
    for a in entries:
        c = a["context"]
        arrow = "v" if a["side"] == "below" else "^"
        price = c["price_at_arm"] or 0.0
        dist = abs(a["level"] - price) / price * 100 if price else 0.0
        atr_d = f"{a['distance_atr']:.2f}" if a.get("distance_atr") is not None else "  - "
        tail = a["expires"] if show_expiry else ""
        print(f"{a['ticker']:<8}{a['direction']:<6}{arrow} {a['level']:>7.2f}"
              f"{a['tolerance']:>7.2f}{price:>9.2f}{dist:>7.1f}%{atr_d:>7}  "
              f"{a['source']:<22}{c['score']:>6.2f}  {tail}")
        if c.get("note"):
            print(f"         ! {c['note']}")


def _print_table(alerts, in_zone, skipped, report_path, min_score):
    header = (f"{'TICKER':<8}{'DIR':<6}{'NIVEL':>9}{'TOL':>7}{'PRECIO':>9}"
              f"{'DIST':>8}{'xATR':>7}  {'ORIGEN':<22}{'SCORE':>6}  CADUCA")

    print(f"\nInforme: {report_path.name}   |   score minimo: {min_score}")
    print(f"Armadas: {len(alerts)}   |   ya en zona: {len(in_zone)}   |   descartadas: {len(skipped)}\n")

    if alerts:
        print("ALERTAS ARMADAS")
        print(header)
        print("-" * 111)
        _rows(alerts)

    if in_zone:
        print("\nYA EN ZONA — sin espera que vigilar, decision de hoy:")
        print(header.replace("  CADUCA", ""))
        print("-" * 101)
        _rows(in_zone, show_expiry=False)

    if skipped:
        print("\nDescartados:")
        for ticker, score, reason in skipped:
            print(f"  {ticker:<8} {score:>5.2f}  {reason}")


def run_arm(day: str = None, min_score: float = DEFAULT_MIN_SCORE,
            write: bool = False, merge: bool = True) -> dict:
    """Arma (y opcionalmente fusiona/escribe) las alertas del informe indicado.

    Punto de entrada programatico — lo usa el orchestrator al cerrar el pipeline
    y tambien main() para la CLI. Devuelve un dict con todo lo calculado.
    """
    report, report_path = _load_report(day)
    try:
        portfolio = json.loads((CONTEXT_DIR / "portfolio.json").read_text(encoding="utf-8"))
    except Exception:
        portfolio = {}

    alerts, in_zone, skipped = build_alerts(report, portfolio, min_score)

    retired = []
    existing = _load_existing() if merge else {}
    if existing.get("alerts"):
        alerts, retired = merge_alerts(alerts, existing, _open_tickers(portfolio))

    if write:
        payload = {
            "generated": datetime.now().isoformat(timespec="seconds"),
            "source_report": report_path.name,
            "min_score": min_score,
            "alerts": alerts,
            # Se persisten aparte: el vigilante no debe armarlas, pero el
            # informe de la manana si debe poder recordarlas.
            "in_zone": in_zone,
        }
        tmp = ALERTS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(ALERTS_FILE)

    return {
        "report_path": report_path,
        "min_score": min_score,
        "alerts": alerts,
        "in_zone": in_zone,
        "skipped": skipped,
        "retired": retired,
        "written": write,
    }


def main():
    ap = argparse.ArgumentParser(description="Arma alertas de precio sobre los WATCH del informe")
    ap.add_argument("--date", help="fecha del informe (YYYY-MM-DD); por defecto el mas reciente")
    ap.add_argument("--min-score", type=float, default=DEFAULT_MIN_SCORE)
    ap.add_argument("--write", action="store_true", help="escribe contex/price_alerts.json")
    ap.add_argument("--no-merge", action="store_true",
                    help="reconstruye desde cero, ignora las alertas ya guardadas")
    args = ap.parse_args()

    res = run_arm(day=args.date, min_score=args.min_score,
                  write=args.write, merge=not args.no_merge)

    _print_table(res["alerts"], res["in_zone"], res["skipped"], res["report_path"], res["min_score"])
    if res["retired"]:
        print("\nRetiradas (fusion):")
        for ticker, reason in res["retired"]:
            print(f"  {ticker:<8} {reason}")

    if res["written"]:
        print(f"\nEscrito: {ALERTS_FILE}")
    else:
        print("\n(previsualizacion — usa --write para guardar)")


if __name__ == "__main__":
    main()
