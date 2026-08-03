"""
Informe de rendimiento (plantilla reutilizable).

Resume el track record de operaciones cerradas en métricas presentables:
suma de plusvalías/minusvalías, % de acierto, drawdown máximo, profit factor
y rentabilidad mensual y acumulada. NO incluye balance de capital ni el
detalle de operaciones — está pensado para enseñar.

Uso:
    python tools/performance_summary.py

Fuente de datos: contex/portfolio.json  ->  "cerradas_semana".
Salida: se imprime por pantalla y se guarda en output/performance_summary_YYYY-MM-DD.txt
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------- #
#  Parámetros (ajusta aquí si cambian las premisas)                            #
# --------------------------------------------------------------------------- #
START_DATE = "2026-01-01"          # inicio del track record
USD_TO_EUR = 0.92                  # 1 USD = 0,92 €
# Base de capital = capital operativo (Broker 1 EUR + Broker 2 USD->EUR),
# se lee dinámicamente de portfolio.json.

ROOT = Path(__file__).resolve().parent.parent
PORTFOLIO_PATH = ROOT / "contex" / "portfolio.json"
OUTPUT_DIR = ROOT / "output"

MESES_ES = {
    "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
    "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
    "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre",
}


def _eur(x: float, signed: bool = True) -> str:
    """Formatea un importe en euros con separadores españoles (1.234,56 €)."""
    s = f"{abs(x):,.2f}".replace(",", "§").replace(".", ",").replace("§", ".")
    sign = ("+" if x >= 0 else "-") if signed else ("" if x >= 0 else "-")
    return f"{sign}{s} €"


def _pct(x: float) -> str:
    s = f"{abs(x):.2f}".replace(".", ",")
    sign = "+" if x >= 0 else "-"
    return f"{sign}{s} %"


def _fecha(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return iso


def _net_eur(trade: dict):
    """P&L neto de una operación convertido a EUR, o None si no hay dato."""
    if trade.get("net_pl_eur") is not None:
        return trade["net_pl_eur"]
    if trade.get("net_pl_usd") is not None:
        return trade["net_pl_usd"] * USD_TO_EUR
    return None


def build_report() -> str:
    data = json.loads(PORTFOLIO_PATH.read_text(encoding="utf-8"))

    # --- Operaciones del periodo --------------------------------------------
    rows = []  # (fecha_cierre, ticker, net_eur)
    for t in data.get("cerradas_semana", []):
        exit_date = t.get("fecha_cierre", "")
        if exit_date < START_DATE:
            continue
        net = _net_eur(t)
        if net is None:
            continue
        rows.append((exit_date, t.get("ticker", "?"), round(net, 2)))
    rows.sort()

    if not rows:
        return "Sin operaciones cerradas en el periodo."

    # --- Base de capital operativo ------------------------------------------
    brokers = data.get("brokers", {})
    base = (
        brokers.get("broker_1", {}).get("cuenta_completa_eur", 0.0)
        + brokers.get("broker_2", {}).get("balance_usd", 0.0) * USD_TO_EUR
    )

    # --- Agregados -----------------------------------------------------------
    wins = [r for r in rows if r[2] > 0]
    losses = [r for r in rows if r[2] <= 0]
    gross_profit = sum(r[2] for r in wins)
    gross_loss = sum(r[2] for r in losses)        # <= 0
    net = gross_profit + gross_loss
    win_rate = len(wins) / len(rows) * 100
    profit_factor = gross_profit / abs(gross_loss) if gross_loss else float("inf")

    # Drawdown máximo sobre la curva de P&L acumulado (ordenada por cierre)
    cum = peak = max_dd = 0.0
    for _, _, n in rows:
        cum += n
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)

    # Rentabilidad mensual
    monthly = defaultdict(float)
    for d, _, n in rows:
        monthly[d[:7]] += n

    # --- Render --------------------------------------------------------------
    first, last = rows[0][0], rows[-1][0]
    L = [
        "=" * 60,
        f"INFORME DE RENDIMIENTO — {_fecha(first)} a {_fecha(last)}",
        "=" * 60,
        "",
        f"Operaciones cerradas:   {len(rows)}",
        "",
        "RESULTADO",
        "-" * 60,
        f"Plusvalías:             {_eur(gross_profit)}",
        f"Minusvalías:            {_eur(gross_loss)}",
        f"Resultado neto:         {_eur(net)}",
        "",
        "MÉTRICAS",
        "-" * 60,
        f"% de acierto:           {win_rate:.1f} %  ({len(wins)}W / {len(losses)}L)".replace(".", ","),
        f"Drawdown máximo:        {_eur(max_dd)}  ({_pct(max_dd / base * 100)})",
        f"Profit factor:          {profit_factor:.2f}".replace(".", ","),
        "",
        f"RENTABILIDAD  (sobre capital operativo {_eur(base, signed=False)})",
        "-" * 60,
    ]
    for month in sorted(monthly):
        m_net = monthly[month]
        yr, mo = month.split("-")
        label = f"{MESES_ES.get(mo, mo)} {yr}:"
        L.append(f"{label:<24}{_pct(m_net / base * 100)}   ({_eur(m_net)})")
    L += [
        "-" * 60,
        f"{'Acumulada:':<24}{_pct(net / base * 100)}   ({_eur(net)})",
        "=" * 60,
    ]
    return "\n".join(L)


def main() -> None:
    report = build_report()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    path = OUTPUT_DIR / f"performance_summary_{today}.txt"
    path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nGuardado en: {path}")


if __name__ == "__main__":
    main()
