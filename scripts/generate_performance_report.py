"""
Genera el PDF de track record de performance de trading.
Lee trades de contex/portfolio.json (cerradas_semana) y genera metricas completas.

Uso:
    python scripts/generate_performance_report.py
    python scripts/generate_performance_report.py --trades contex/trades_historico.json
"""
import argparse
import io
import json
import os
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    HRFlowable, Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)

# ── Colores ──────────────────────────────────────────────────────────────────
C_BG    = "#0d1117"
C_GREEN = "#238636"
C_RED   = "#da3633"
C_BLUE  = "#1f6feb"
C_GOLD  = "#d29922"
C_GRAY  = "#8b949e"
C_LIGHT = "#f0f6fc"
C_WHITE = "#ffffff"
C_PANEL = "#f6f8fa"

MPL_BG   = "#f6f8fa"
MPL_GRID = "#e1e4e8"

# ── Estilos ReportLab ────────────────────────────────────────────────────────
def _s(name, **kw):
    return ParagraphStyle(name, **kw)

TITLE  = _s("tt", fontSize=26, leading=32, fontName="Helvetica-Bold",
             textColor=colors.HexColor(C_BG), alignment=TA_CENTER, spaceAfter=4)
SUB    = _s("sub", fontSize=11, leading=16, fontName="Helvetica",
             textColor=colors.HexColor(C_GRAY), alignment=TA_CENTER, spaceAfter=2)
H1     = _s("h1", fontSize=14, leading=18, fontName="Helvetica-Bold",
             textColor=colors.HexColor(C_BG), spaceBefore=16, spaceAfter=6)
H2     = _s("h2", fontSize=10, leading=14, fontName="Helvetica-Bold",
             textColor=colors.HexColor(C_BLUE), spaceBefore=10, spaceAfter=4)
BODY   = _s("bd", fontSize=9, leading=13, fontName="Helvetica",
             textColor=colors.HexColor(C_BG), alignment=TA_JUSTIFY, spaceAfter=4)
SMALL  = _s("sm", fontSize=8, leading=11, fontName="Helvetica",
             textColor=colors.HexColor(C_GRAY), alignment=TA_CENTER)
MONO   = _s("mo", fontSize=8.5, leading=12, fontName="Courier",
             textColor=colors.HexColor("#24292f"),
             backColor=colors.HexColor(C_PANEL), borderPadding=6)

def HR():
    return HRFlowable(width="100%", thickness=1,
                      color=colors.HexColor("#e1e4e8"), spaceAfter=8, spaceBefore=2)

def sp(h=6):
    return Spacer(1, h)

def P(t, s=None):
    return Paragraph(t, s or BODY)

# ── Carga y normalización de trades ─────────────────────────────────────────
def load_trades(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Soporta: trades_historico.json (trades[]), portfolio.json (cerradas_semana[]), o lista directa
    raw = (data.get("trades")
           or data.get("cerradas_semana")
           or data) if isinstance(data, dict) else data

    trades = []
    for t in raw:
        # Normalizar P&L a float único (preferir net, luego gross)
        pl = (t.get("net_pl_eur") or t.get("net_pl_usd")
              or t.get("gross_pl_usd") or t.get("pl") or 0)
        try:
            pl = float(pl)
        except (TypeError, ValueError):
            pl = 0.0

        entry = float(t.get("entrada_usd") or t.get("entrada") or 0)
        close = float(t.get("cierre_usd") or t.get("cierre") or 0)
        qty   = float(t.get("cantidad") or 1)

        # Riesgo aproximado: diferencia entrada–SL si disponible, sino 1R = |entry–close|
        riesgo = abs(entry - close) * qty if entry and close else abs(pl)
        if riesgo == 0:
            riesgo = abs(pl) if pl != 0 else 1.0

        fecha_str = t.get("fecha_cierre") or t.get("fecha") or ""
        try:
            fecha = datetime.strptime(fecha_str[:10], "%Y-%m-%d").date()
        except ValueError:
            fecha = date.today()

        trades.append({
            "ticker":    t.get("ticker", "?"),
            "broker":    t.get("broker", "?"),
            "direccion": t.get("direccion", "long"),
            "pl":        pl,
            "riesgo":    riesgo,
            "fecha":     fecha,
            "mes":       fecha.strftime("%Y-%m"),
            "nota":      t.get("nota", ""),
        })

    trades.sort(key=lambda x: x["fecha"])
    return trades

# ── Métricas ─────────────────────────────────────────────────────────────────
def compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {}

    pls     = [t["pl"] for t in trades]
    wins    = [p for p in pls if p > 0]
    losses  = [p for p in pls if p < 0]

    total_pl      = sum(pls)
    win_rate      = len(wins) / len(pls) * 100 if pls else 0
    avg_win       = np.mean(wins)  if wins   else 0
    avg_loss      = np.mean(losses) if losses else 0
    gross_profit  = sum(wins)
    gross_loss    = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss else float("inf")

    rr_ratios = []
    for t in trades:
        if t["riesgo"] > 0 and t["pl"] != 0:
            rr_ratios.append(t["pl"] / t["riesgo"])
    avg_rr = np.mean(rr_ratios) if rr_ratios else 0

    # Equity curve & drawdown
    equity = np.cumsum([0] + pls)
    peak   = np.maximum.accumulate(equity)
    dd     = equity - peak
    max_dd = float(np.min(dd))
    max_dd_pct = (max_dd / peak[np.argmin(dd)]) * 100 if peak[np.argmin(dd)] != 0 else 0

    # Por mes
    monthly: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        monthly[t["mes"]].append(t["pl"])

    monthly_summary = {
        m: {
            "pl": sum(v), "trades": len(v),
            "wins": sum(1 for x in v if x > 0),
            "wr": sum(1 for x in v if x > 0) / len(v) * 100,
        }
        for m, v in sorted(monthly.items())
    }

    # Racha
    best_streak = worst_streak = cur = 0
    for p in pls:
        if p > 0:
            cur = max(cur + 1, 1)
        else:
            cur = min(cur - 1, -1)
        best_streak  = max(best_streak,  cur)
        worst_streak = min(worst_streak, cur)

    return {
        "n_trades": len(pls), "n_wins": len(wins), "n_losses": len(losses),
        "total_pl": total_pl, "win_rate": win_rate,
        "avg_win": avg_win, "avg_loss": avg_loss,
        "profit_factor": profit_factor, "avg_rr": avg_rr,
        "gross_profit": gross_profit, "gross_loss": gross_loss,
        "max_dd": max_dd, "max_dd_pct": max_dd_pct,
        "equity": equity.tolist(), "drawdown": dd.tolist(),
        "best_streak": best_streak, "worst_streak": worst_streak,
        "monthly": monthly_summary,
        "pls": pls,
        "fecha_ini": trades[0]["fecha"], "fecha_fin": trades[-1]["fecha"],
    }

# ── Gráficos matplotlib → buffer PNG ─────────────────────────────────────────
def fig_to_image(fig, w_cm: float, h_cm: float) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor=MPL_BG, edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=w_cm * cm, height=h_cm * cm)


def chart_equity(m: dict) -> Image:
    equity = m["equity"]
    x = list(range(len(equity)))
    fig, ax = plt.subplots(figsize=(10, 3.2), facecolor=MPL_BG)
    ax.set_facecolor(MPL_BG)

    pos_mask = [equity[i] >= 0 for i in range(len(equity))]
    ax.fill_between(x, 0, equity,
                    where=[e >= 0 for e in equity],
                    alpha=0.18, color=C_GREEN, interpolate=True)
    ax.fill_between(x, 0, equity,
                    where=[e < 0 for e in equity],
                    alpha=0.18, color=C_RED, interpolate=True)
    ax.plot(x, equity, color=C_BLUE, linewidth=1.8, zorder=3)
    ax.axhline(0, color=C_GRAY, linewidth=0.8, linestyle="--", alpha=0.6)

    ax.set_title("Curva de Capital Acumulada", fontsize=10, fontweight="bold",
                 color=C_BG, pad=8)
    ax.set_xlabel("N.º Trade", fontsize=8, color=C_GRAY)
    ax.set_ylabel("P&L acumulado", fontsize=8, color=C_GRAY)
    ax.tick_params(colors=C_GRAY, labelsize=7)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.0f"))
    for spine in ax.spines.values():
        spine.set_edgecolor(MPL_GRID)
    ax.grid(True, color=MPL_GRID, linewidth=0.5, linestyle="-")

    # Anotar máximo y mínimo
    peak_idx = int(np.argmax(equity))
    trough_idx = int(np.argmin(equity))
    ax.annotate(f"Max {equity[peak_idx]:+.0f}",
                xy=(peak_idx, equity[peak_idx]),
                xytext=(peak_idx, equity[peak_idx] + abs(equity[peak_idx]) * 0.12 + 5),
                fontsize=7, color=C_GREEN, ha="center",
                arrowprops=dict(arrowstyle="->", color=C_GREEN, lw=0.8))
    fig.tight_layout()
    return fig_to_image(fig, 17, 5.5)


def chart_drawdown(m: dict) -> Image:
    dd = m["drawdown"]
    x  = list(range(len(dd)))
    fig, ax = plt.subplots(figsize=(10, 2.4), facecolor=MPL_BG)
    ax.set_facecolor(MPL_BG)
    ax.fill_between(x, 0, dd, alpha=0.35, color=C_RED)
    ax.plot(x, dd, color=C_RED, linewidth=1.2)
    ax.axhline(0, color=C_GRAY, linewidth=0.6)

    ax.set_title("Drawdown", fontsize=10, fontweight="bold", color=C_BG, pad=6)
    ax.set_xlabel("N.º Trade", fontsize=8, color=C_GRAY)
    ax.set_ylabel("Drawdown", fontsize=8, color=C_GRAY)
    ax.tick_params(colors=C_GRAY, labelsize=7)
    ax.yaxis.set_major_formatter(mtick.FormatStrFormatter("%.0f"))
    for spine in ax.spines.values():
        spine.set_edgecolor(MPL_GRID)
    ax.grid(True, color=MPL_GRID, linewidth=0.5)
    fig.tight_layout()
    return fig_to_image(fig, 17, 4)


def chart_histogram(m: dict) -> Image:
    pls = m["pls"]
    fig, ax = plt.subplots(figsize=(7, 3.2), facecolor=MPL_BG)
    ax.set_facecolor(MPL_BG)

    bins = min(20, max(8, len(pls) // 3))
    wins_data   = [p for p in pls if p >= 0]
    losses_data = [p for p in pls if p < 0]

    if wins_data:
        ax.hist(wins_data, bins=bins, color=C_GREEN, alpha=0.75,
                label=f"Wins ({len(wins_data)})", edgecolor="white", linewidth=0.4)
    if losses_data:
        ax.hist(losses_data, bins=bins, color=C_RED, alpha=0.75,
                label=f"Losses ({len(losses_data)})", edgecolor="white", linewidth=0.4)

    ax.axvline(np.mean(pls), color=C_BLUE, linewidth=1.4,
               linestyle="--", label=f"Media {np.mean(pls):+.1f}")
    ax.axvline(0, color=C_GRAY, linewidth=0.8, alpha=0.6)

    ax.set_title("Histograma de Retornos", fontsize=10, fontweight="bold",
                 color=C_BG, pad=6)
    ax.set_xlabel("P&L por trade", fontsize=8, color=C_GRAY)
    ax.set_ylabel("Frecuencia", fontsize=8, color=C_GRAY)
    ax.tick_params(colors=C_GRAY, labelsize=7)
    ax.legend(fontsize=7, framealpha=0.6)
    for spine in ax.spines.values():
        spine.set_edgecolor(MPL_GRID)
    ax.grid(True, color=MPL_GRID, linewidth=0.5, axis="y")
    fig.tight_layout()
    return fig_to_image(fig, 9.5, 5.5)


def chart_monthly_bar(m: dict) -> Image:
    monthly = m["monthly"]
    months  = list(monthly.keys())
    pls_m   = [monthly[mo]["pl"] for mo in months]
    bar_colors = [C_GREEN if p >= 0 else C_RED for p in pls_m]
    labels  = [mo[5:] + "/" + mo[2:4] for mo in months]  # MM/YY

    fig, ax = plt.subplots(figsize=(7, 3.2), facecolor=MPL_BG)
    ax.set_facecolor(MPL_BG)
    bars = ax.bar(labels, pls_m, color=bar_colors, alpha=0.85,
                  edgecolor="white", linewidth=0.4)
    ax.axhline(0, color=C_GRAY, linewidth=0.8)

    for bar, v in zip(bars, pls_m):
        ax.text(bar.get_x() + bar.get_width() / 2,
                v + (2 if v >= 0 else -4),
                f"{v:+.0f}", ha="center", va="bottom" if v >= 0 else "top",
                fontsize=7, color=C_BG, fontweight="bold")

    ax.set_title("P&L Mensual", fontsize=10, fontweight="bold", color=C_BG, pad=6)
    ax.set_ylabel("P&L", fontsize=8, color=C_GRAY)
    ax.tick_params(colors=C_GRAY, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(MPL_GRID)
    ax.grid(True, color=MPL_GRID, linewidth=0.5, axis="y")
    fig.tight_layout()
    return fig_to_image(fig, 9.5, 5.5)

# ── KPI Card helper ──────────────────────────────────────────────────────────
def kpi_table(cells: list[tuple]) -> Table:
    """cells: list of (label, value, color_hex)"""
    cs = ParagraphStyle("cv", fontSize=18, fontName="Helvetica-Bold",
                        alignment=TA_CENTER, leading=22)
    cl = ParagraphStyle("cl", fontSize=7.5, fontName="Helvetica",
                        textColor=colors.HexColor(C_GRAY), alignment=TA_CENTER, leading=10)

    data = []
    row_vals, row_labels = [], []
    for label, value, col in cells:
        row_vals.append(Paragraph(f'<font color="{col}">{value}</font>', cs))
        row_labels.append(Paragraph(label, cl))

    data = [row_vals, row_labels]
    n = len(cells)
    col_w = 17 * cm / n
    t = Table(data, colWidths=[col_w] * n, rowHeights=[1.2*cm, 0.5*cm])
    t.setStyle(TableStyle([
        ("ALIGN",        (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ("BACKGROUND",   (0, 0), (-1, -1), colors.HexColor(C_PANEL)),
        ("GRID",         (0, 0), (-1, -1), 0.5, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.HexColor(C_PANEL)]),
    ]))
    return t

# ── Tabla mensual ─────────────────────────────────────────────────────────────
def monthly_table(monthly: dict) -> Table:
    hs = ParagraphStyle("mh", fontSize=8.5, fontName="Helvetica-Bold",
                        textColor=colors.white, alignment=TA_CENTER)
    cs = ParagraphStyle("mc", fontSize=8.5, fontName="Helvetica",
                        textColor=colors.HexColor(C_BG), alignment=TA_CENTER, leading=12)

    header = [P("Mes", hs), P("Trades", hs), P("Wins", hs),
              P("Win Rate", hs), P("P&L", hs)]
    rows = [header]
    for mo, v in sorted(monthly.items()):
        pl_str = f"{v['pl']:+.2f}"
        pl_col = C_GREEN if v["pl"] >= 0 else C_RED
        rows.append([
            P(datetime.strptime(mo, "%Y-%m").strftime("%B %Y"), cs),
            P(str(v["trades"]), cs),
            P(str(v["wins"]), cs),
            P(f"{v['wr']:.1f}%", cs),
            Paragraph(f'<font color="{pl_col}"><b>{pl_str}</b></font>', cs),
        ])

    # Totals row
    total_pl = sum(v["pl"] for v in monthly.values())
    total_t  = sum(v["trades"] for v in monthly.values())
    total_w  = sum(v["wins"] for v in monthly.values())
    avg_wr   = total_w / total_t * 100 if total_t else 0
    pl_col   = C_GREEN if total_pl >= 0 else C_RED
    tf = ParagraphStyle("mf", fontSize=8.5, fontName="Helvetica-Bold",
                        textColor=colors.HexColor(C_BG), alignment=TA_CENTER)
    rows.append([
        P("TOTAL", tf),
        P(str(total_t), tf),
        P(str(total_w), tf),
        P(f"{avg_wr:.1f}%", tf),
        Paragraph(f'<font color="{pl_col}">{total_pl:+.2f}</font>', tf),
    ])

    cws = [3.8*cm, 2.5*cm, 2.5*cm, 3*cm, 3*cm]
    t = Table(rows, colWidths=cws)
    ts = TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor(C_BG)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 1), (-2, -1),
         [colors.white, colors.HexColor(C_PANEL)]),
        ("BACKGROUND",    (0, -1), (-1, -1), colors.HexColor("#e8f4fd")),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ])
    t.setStyle(ts)
    return t

# ── Tabla últimos trades ──────────────────────────────────────────────────────
def recent_trades_table(trades: list[dict], n: int = 15) -> Table:
    hs = ParagraphStyle("rh", fontSize=8, fontName="Helvetica-Bold",
                        textColor=colors.white, alignment=TA_CENTER)
    cs = ParagraphStyle("rc", fontSize=8, fontName="Helvetica",
                        textColor=colors.HexColor(C_BG), alignment=TA_CENTER, leading=11)

    header = [P("Fecha", hs), P("Ticker", hs), P("Dir.", hs),
              P("Broker", hs), P("P&L", hs)]
    rows = [header]
    for t in trades[-n:]:
        pl_col = C_GREEN if t["pl"] >= 0 else C_RED
        broker_short = t["broker"].replace("broker_", "B")
        rows.append([
            P(t["fecha"].strftime("%d/%m/%y"), cs),
            P(f"<b>{t['ticker']}</b>", cs),
            P(t["direccion"].upper(), cs),
            P(broker_short, cs),
            Paragraph(f'<font color="{pl_col}"><b>{t["pl"]:+.2f}</b></font>', cs),
        ])

    cws = [2.4*cm, 2.4*cm, 1.8*cm, 2.4*cm, 2.4*cm]
    t = Table(rows, colWidths=cws)
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor(C_BG)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor(C_PANEL)]),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return t

# ── BUILD PDF ─────────────────────────────────────────────────────────────────
def build(trades_path: str, output_path: str):
    trades  = load_trades(trades_path)
    m       = compute_metrics(trades)
    monthly = m["monthly"]

    os.makedirs("output", exist_ok=True)
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2*cm, bottomMargin=2*cm,
        title="Trading Track Record",
        author="Trading Agent",
    )

    story = []

    # ── PORTADA / HEADER ──────────────────────────────────────────────────────
    story += [sp(20)]
    story += [P("Trading Track Record", TITLE)]
    story += [P(
        f"{m['fecha_ini'].strftime('%d %b %Y')}  —  {m['fecha_fin'].strftime('%d %b %Y')}  "
        f"·  {m['n_trades']} trades  ·  {len(monthly)} mes{'es' if len(monthly) > 1 else ''}",
        SUB)]
    story += [sp(10)]
    story += [HR()]
    story += [sp(8)]

    # ── KPIs PRINCIPALES ─────────────────────────────────────────────────────
    pf_str = f"{m['profit_factor']:.2f}" if m["profit_factor"] != float("inf") else "∞"
    pl_col = C_GREEN if m["total_pl"] >= 0 else C_RED
    story += [kpi_table([
        ("P&L Total", f"{m['total_pl']:+.2f}", pl_col),
        ("Win Rate",  f"{m['win_rate']:.1f}%",
         C_GREEN if m["win_rate"] >= 50 else C_RED),
        ("Profit Factor", pf_str,
         C_GREEN if m["profit_factor"] >= 1 else C_RED),
        ("Avg R/R",   f"{m['avg_rr']:+.2f}",
         C_GREEN if m["avg_rr"] >= 0 else C_RED),
        ("Max DD",    f"{m['max_dd']:+.2f}",
         C_RED if m["max_dd"] < 0 else C_GRAY),
    ])]
    story += [sp(6)]
    story += [kpi_table([
        ("Trades Totales", str(m["n_trades"]),        C_BLUE),
        ("Ganadores",      str(m["n_wins"]),           C_GREEN),
        ("Perdedores",     str(m["n_losses"]),         C_RED),
        ("Ganancia Media", f"{m['avg_win']:+.2f}",    C_GREEN),
        ("Perdida Media",  f"{m['avg_loss']:+.2f}",   C_RED),
    ])]
    story += [sp(6)]
    story += [kpi_table([
        ("Gross Profit",   f"{m['gross_profit']:.2f}", C_GREEN),
        ("Gross Loss",     f"{m['gross_loss']:.2f}",   C_RED),
        ("Max DD %",       f"{m['max_dd_pct']:.1f}%",  C_RED),
        ("Mejor racha",    f"+{m['best_streak']} wins", C_GREEN),
        ("Peor racha",     f"{m['worst_streak']} loss", C_RED),
    ])]

    story += [sp(14)]

    # ── CURVA DE CAPITAL + DRAWDOWN ───────────────────────────────────────────
    story += [P("Curva de Capital y Drawdown", H1)]
    story += [HR()]
    story += [chart_equity(m)]
    story += [sp(8)]
    story += [chart_drawdown(m)]

    story += [PageBreak()]

    # ── HISTOGRAMA + BARRAS MENSUALES ─────────────────────────────────────────
    story += [P("Distribucion de Retornos  &  P&L Mensual", H1)]
    story += [HR()]

    hist_img    = chart_histogram(m)
    monthly_img = chart_monthly_bar(m)

    side_table = Table([[hist_img, monthly_img]],
                       colWidths=[9.8*cm, 9.8*cm])
    side_table.setStyle(TableStyle([
        ("ALIGN",  (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story += [side_table]

    story += [sp(10)]

    # ── RESUMEN MENSUAL ────────────────────────────────────────────────────────
    story += [KeepTogether([
        P("Resumen por Mes", H1),
        HR(),
        monthly_table(monthly),
    ])]

    story += [PageBreak()]

    # ── PÁGINA 3: TRADES + BROKER ─────────────────────────────────────────────
    story += [P("Ultimos Trades Cerrados", H1)]
    story += [HR()]
    story += [recent_trades_table(trades, 20)]

    story += [sp(16)]

    # Desglose por broker — tabla ancha, no en dos columnas
    broker_pls = defaultdict(list)
    for t in trades:
        broker_pls[t["broker"]].append(t["pl"])

    bs = ParagraphStyle("brs", fontSize=9, fontName="Helvetica",
                        textColor=colors.HexColor(C_BG), alignment=TA_CENTER, leading=13)
    bh = ParagraphStyle("brh", fontSize=9, fontName="Helvetica-Bold",
                        textColor=colors.white, alignment=TA_CENTER)

    broker_rows = []
    for br, pls_b in sorted(broker_pls.items()):
        wins_b  = sum(1 for p in pls_b if p > 0)
        gross_p = sum(p for p in pls_b if p > 0)
        gross_l = abs(sum(p for p in pls_b if p < 0))
        pf      = f"{gross_p/gross_l:.2f}" if gross_l else "∞"
        pl_col  = C_GREEN if sum(pls_b) >= 0 else C_RED
        broker_rows.append([
            P(br.replace("broker_", "Broker "), bs),
            P(str(len(pls_b)), bs),
            P(f"{wins_b / len(pls_b) * 100:.1f}%", bs),
            P(pf, bs),
            Paragraph(f'<font color="{pl_col}"><b>{sum(pls_b):+.2f}</b></font>', bs),
        ])

    broker_data = [[P(h, bh) for h in ["Broker", "Trades", "Win Rate", "Profit Factor", "P&L Total"]]] + broker_rows
    bt = Table(broker_data, colWidths=[4*cm, 3*cm, 3.5*cm, 3.5*cm, 3.2*cm])
    bt.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor(C_BLUE)),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor(C_PANEL)]),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]))

    story += [KeepTogether([
        P("Desglose por Broker", H2),
        bt,
    ])]

    story += [sp(24)]
    story += [HR()]
    story += [P(
        f"Trading Agent  ·  Track Record generado el {date.today().strftime('%d/%m/%Y')}  "
        f"·  {m['n_trades']} trades  ·  "
        f"{m['fecha_ini'].strftime('%d/%m/%Y')} — {m['fecha_fin'].strftime('%d/%m/%Y')}",
        SMALL)]

    doc.build(story)
    print(f"PDF generado: {output_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--trades",  default="contex/trades_historico.json",
                        help="JSON con los trades (default: contex/portfolio.json)")
    parser.add_argument("--output",  default="output/track_record.pdf",
                        help="Ruta del PDF de salida")
    args = parser.parse_args()
    build(args.trades, args.output)
