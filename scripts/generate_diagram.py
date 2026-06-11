"""Diagrama de flujo de ejecución del trading agent."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from pathlib import Path

OUTPUT = Path(__file__).parent.parent / "output" / "pipeline_diagram.pdf"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

fig, ax = plt.subplots(figsize=(18, 26))
ax.set_xlim(0, 18)
ax.set_ylim(0, 26)
ax.axis("off")
fig.patch.set_facecolor("#FAFAFA")

# ── Helpers ───────────────────────────────────────────────────────────────────
def box(ax, x, y, w, h, title, subtitle=None, color="#1565C0", fontsize=9):
    rect = FancyBboxPatch((x, y), w, h,
                          boxstyle="round,pad=0.12",
                          facecolor=color, edgecolor="#FFFFFF",
                          linewidth=2, zorder=3)
    ax.add_patch(rect)
    ty = y + h/2 + (0.15 if subtitle else 0)
    ax.text(x + w/2, ty, title, ha="center", va="center",
            fontsize=fontsize, fontweight="bold", color="white", zorder=4, wrap=True)
    if subtitle:
        ax.text(x + w/2, y + h/2 - 0.18, subtitle,
                ha="center", va="center", fontsize=7.5,
                color="white", alpha=0.88, zorder=4)

def diamond(ax, cx, cy, w, h, label, color="#E65100"):
    from matplotlib.patches import Polygon
    pts = [(cx, cy+h/2), (cx+w/2, cy), (cx, cy-h/2), (cx-w/2, cy)]
    poly = Polygon(pts, closed=True, facecolor=color,
                   edgecolor="white", linewidth=2, zorder=3)
    ax.add_patch(poly)
    ax.text(cx, cy, label, ha="center", va="center",
            fontsize=8.5, fontweight="bold", color="white", zorder=4)

def arrow_v(ax, x, y1, y2, color="#455A64", lw=2):
    ax.annotate("", xy=(x, y2), xytext=(x, y1),
                arrowprops=dict(arrowstyle="-|>", color=color,
                                lw=lw, mutation_scale=14), zorder=2)

def arrow_h(ax, x1, x2, y, color="#455A64", lw=1.8):
    ax.annotate("", xy=(x2, y), xytext=(x1, y),
                arrowprops=dict(arrowstyle="-|>", color=color,
                                lw=lw, mutation_scale=12), zorder=2)

def label_arrow(ax, x, y, text, color="#455A64"):
    ax.text(x, y, text, ha="center", va="center", fontsize=7.5,
            color=color, style="italic",
            bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.8))

def side_box(ax, x, y, w, h, title, color, fontsize=8):
    rect = FancyBboxPatch((x, y), w, h,
                          boxstyle="round,pad=0.1",
                          facecolor=color, edgecolor="white",
                          linewidth=1.5, alpha=0.85, zorder=3)
    ax.add_patch(rect)
    ax.text(x + w/2, y + h/2, title, ha="center", va="center",
            fontsize=fontsize, fontweight="bold", color="white", zorder=4)

# ── Título ────────────────────────────────────────────────────────────────────
ax.text(9, 25.5, "SWING TRADING AGENT", ha="center", va="center",
        fontsize=18, fontweight="bold", color="#0D47A1")
ax.text(9, 25.1, "Flujo de Ejecución del Pipeline", ha="center", va="center",
        fontsize=11, color="#455A64")

# ═══════════════════════════════════════════════════════════════════════════
# COLUMNA IZQUIERDA — PIPELINE PRINCIPAL (x centro = 6.5)
# ═══════════════════════════════════════════════════════════════════════════
CX = 6.5   # centro columna pipeline
BW = 5.0   # ancho box
BH = 0.85  # alto box
BX = CX - BW/2

# 1. TRIGGER
box(ax, BX, 23.8, BW, BH, "TASK SCHEDULER",
    "Lun-Vie 15:45 (morning) · 20:30 (evening)", "#37474F")
arrow_v(ax, CX, 23.8, 23.3, "#37474F")

# 2. ORCHESTRATOR
box(ax, BX, 22.4, BW, BH, "ORCHESTRATOR",
    "main.py → TradingOrchestrator.run_daily_pipeline()", "#0D47A1")
arrow_v(ax, CX, 22.4, 21.9, "#0D47A1")

# Datos de mercado side
side_box(ax, 0.2, 21.1, 2.0, 0.7, "Alpaca API\n(quotes batch)", "#1565C0")
side_box(ax, 0.2, 20.2, 2.0, 0.7, "yfinance\n(VIX + fallback)", "#1976D2")
ax.annotate("", xy=(BX, 21.5), xytext=(2.2, 21.5),
            arrowprops=dict(arrowstyle="-|>", color="#1565C0", lw=1.5, mutation_scale=11), zorder=2)

# 3. MARKET SCANNER
box(ax, BX, 21.0, BW, BH, "① MARKET SCANNER",
    "Descarga precios 107 tickers · filtra por precio/volumen · Claude prioriza top 30", "#1565C0")
label_arrow(ax, CX, 20.82, "→ 50 candidatos ScanCandidate(initial_score)")
arrow_v(ax, CX, 21.0, 20.4, "#1565C0")

# Finviz side
side_box(ax, 0.2, 19.3, 2.0, 0.7, "Finviz\nfundamentals", "#6A1B9A")
ax.annotate("", xy=(BX, 19.7), xytext=(2.2, 19.6),
            arrowprops=dict(arrowstyle="-|>", color="#6A1B9A", lw=1.5, mutation_scale=11), zorder=2)

# 4. FUNDAMENTAL ANALYST
box(ax, BX, 19.1, BW, BH, "② FUNDAMENTAL ANALYST",
    "Earnings · short float · insider · analyst recom · target price", "#6A1B9A")
label_arrow(ax, CX, 18.92, "→ fundamental_score (0-10) · bloquea earnings < 3d")
arrow_v(ax, CX, 19.1, 18.5, "#6A1B9A")

# OHLCV side
side_box(ax, 0.2, 17.5, 2.0, 0.7, "Alpaca OHLCV\n90 días D1", "#00695C")
ax.annotate("", xy=(BX, 17.9), xytext=(2.2, 17.8),
            arrowprops=dict(arrowstyle="-|>", color="#00695C", lw=1.5, mutation_scale=11), zorder=2)

# 5. TECHNICAL ANALYST
box(ax, BX, 17.2, BW, BH, "③ TECHNICAL ANALYST",
    "RSI · MACD · EMA 9/21/50 · BB · ATR · ADX · soporte/resistencia + Claude", "#00695C")
label_arrow(ax, CX, 17.02, "→ ta_score (35% del composite)")
arrow_v(ax, CX, 17.2, 16.6, "#00695C")

# News side
side_box(ax, 0.2, 15.7, 2.0, 0.7, "News APIs\n+ Claude", "#E65100")
ax.annotate("", xy=(BX, 16.1), xytext=(2.2, 16.0),
            arrowprops=dict(arrowstyle="-|>", color="#E65100", lw=1.5, mutation_scale=11), zorder=2)

# 6. SENTIMENT ANALYST
box(ax, BX, 15.3, BW, BH, "④ NEWS SENTIMENT ANALYST",
    "Noticias · catalizadores · risk flags · Claude evalúa", "#E65100")
label_arrow(ax, CX, 15.12, "→ sentiment_score_normalized (15%)")
arrow_v(ax, CX, 15.3, 14.7, "#E65100")

# 7. RISK MANAGER
box(ax, BX, 13.8, BW, BH, "⑤ RISK MANAGER",
    "ATR SL · 2x targets · position sizing · R:R ≥ mínimo · MIN/MAX_RISK $500-600", "#B71C1C")
label_arrow(ax, CX, 13.62, "→ risk_score (20%) · entry/SL/TP/shares")
arrow_v(ax, CX, 13.8, 13.2, "#B71C1C")

# Merge & Rank
box(ax, BX, 12.3, BW, BH, "_merge_and_rank()",
    "Scan 15% + Fund 15% + TA 35% + Sentiment 15% + Risk 20% = composite_score", "#1A237E")
label_arrow(ax, CX, 12.12, "→ FinalCandidate ordenados por composite_score")
arrow_v(ax, CX, 12.3, 11.7, "#1A237E")

# Threshold
diamond(ax, CX, 11.3, 3.2, 0.8, "composite_score ≥ 6.0?", "#37474F")
# No branch
ax.annotate("", xy=(BX - 0.5, 11.3), xytext=(CX - 1.6, 11.3),
            arrowprops=dict(arrowstyle="-|>", color="#C62828", lw=1.5, mutation_scale=11))
ax.text(BX - 0.8, 11.4, "NO\n→ WATCH", ha="center", fontsize=8, color="#C62828", fontweight="bold")
arrow_v(ax, CX, 11.3, 10.7, "#2E7D32", lw=2)
ax.text(CX + 0.15, 11.0, "SÍ", ha="left", fontsize=8.5, color="#2E7D32", fontweight="bold")

# 8. REPORT WRITER
box(ax, BX, 9.8, BW, BH, "⑥ REPORT WRITER",
    "Claude redacta narrativa · BUY / STRONG BUY · dirección · broker asignado", "#2E7D32")
arrow_v(ax, CX, 9.8, 9.2, "#2E7D32")

# OUTPUTS
box(ax, BX, 8.3, 2.3, 0.75, "report_YYYY-MM-DD.txt", None, "#388E3C", fontsize=8)
box(ax, BX + 2.5, 8.3, 2.3, 0.75, "report_YYYY-MM-DD.json", None, "#388E3C", fontsize=8)
box(ax, BX, 7.3, 2.3, 0.75, "Telegram\ninforme pipeline", None, "#1976D2", fontsize=8)
box(ax, BX + 2.5, 7.3, 2.3, 0.75, "daily_state_YYYY-MM-DD\n.json (contexto)", None, "#6A1B9A", fontsize=8)

arrow_h(ax, CX - 1.0, BX + 0.5, 8.9, "#388E3C")
arrow_h(ax, CX + 0.5, BX + 2.6, 8.9, "#388E3C")
arrow_h(ax, CX - 1.0, BX + 0.5, 7.9, "#1976D2")
arrow_h(ax, CX + 0.5, BX + 2.6, 7.9, "#6A1B9A")

# ═══════════════════════════════════════════════════════════════════════════
# COLUMNA DERECHA — ENTRY SCANNER + BROKERS (x centro = 13.5)
# ═══════════════════════════════════════════════════════════════════════════
RX = 10.0
RW = 7.5
RC = RX + RW/2

# Separador
ax.plot([9.5, 9.5], [2.0, 25.0], color="#B0BEC5", lw=1.5, linestyle="--", zorder=1)
ax.text(9.5, 25.3, "│", ha="center", fontsize=10, color="#B0BEC5")

# ENTRY SCANNER título
ax.text(RC, 25.1, "ENTRY SCANNER (Telegram /scan)", ha="center",
        fontsize=11, fontweight="bold", color="#00695C")

# Trigger
box(ax, RX + 0.5, 23.8, RW - 1, BH, "Telegram Bot /scan",
    "Comando manual · cualquier momento", "#00695C")
arrow_v(ax, RC, 23.8, 23.3, "#00695C")

# Fetch H4
side_box(ax, 10.0, 22.3, 1.8, 0.65, "Alpaca\nH4 45d", "#00838F")
ax.annotate("", xy=(RX + 2.0, 22.65), xytext=(11.8, 22.65),
            arrowprops=dict(arrowstyle="-|>", color="#00838F", lw=1.5, mutation_scale=11))
box(ax, RX + 2.1, 22.3, RW - 3.0, BH, "Fetch OHLCV H4",
    "45 días · 107 tickers watchlist · excluye cartera", "#00838F")
arrow_v(ax, RC, 22.3, 21.7, "#00838F")

# Indicadores
box(ax, RX + 0.5, 21.0, RW - 1, BH, "Cálculo Indicadores (Python)",
    "EMA9/21 · SMA20 · MACD · RSI · ATR slope · Vol ratio", "#00838F")
arrow_v(ax, RC, 21.0, 20.4, "#00838F")

# Señales
box(ax, RX + 0.5, 19.5, RW - 1, 0.75, "SEÑALES LONG", None, "#2E7D32", fontsize=8)
ax.text(RC, 19.2, "EMA_CROSS_UP · MACD_BULL_CROSS · RSI_RECOVERY · PULLBACK_EMA · BREAKOUT · VOLUME_SURGE_UP",
        ha="center", va="top", fontsize=6.8, color="#1B5E20",
        bbox=dict(boxstyle="round", fc="#E8F5E9", ec="#A5D6A7", alpha=0.9))

box(ax, RX + 0.5, 18.4, RW - 1, 0.75, "SEÑALES SHORT", None, "#B71C1C", fontsize=8)
ax.text(RC, 18.1, "EMA_CROSS_DOWN · MACD_BEAR_CROSS · RSI_REJECT · BREAKDOWN · BELOW_SMA20 · VOLUME_SURGE_DN",
        ha="center", va="top", fontsize=6.8, color="#7F0000",
        bbox=dict(boxstyle="round", fc="#FFEBEE", ec="#EF9A9A", alpha=0.9))

arrow_v(ax, RC, 18.4, 17.8, "#00838F")

# Filtros
box(ax, RX + 0.5, 17.0, RW - 1, 0.65, "FILTROS", None, "#37474F", fontsize=8.5)
filtros = [
    "① Precio ≥ $2 · Vol medio H4 ≥ 50.000",
    "② Ancla: al menos 1 señal de calidad (MACD/RSI/BREAKOUT)",
    "③ RSI_MAX_LONG < 74 · RSI_MIN_SHORT > 35",
    "④ Contradicción: mantiene solo la dirección más fuerte",
    "⑤ Mínimo 3 señales (long) · 3 señales (short)",
]
for i, f in enumerate(filtros):
    ax.text(RX + 0.7, 16.8 - i*0.32, f"• {f}", fontsize=7.5, color="#212121", va="top")

arrow_v(ax, RC, 17.0, 15.5, "#37474F")

# Alerta
box(ax, RX + 0.5, 14.7, RW - 1, 0.65, "Telegram Alert [H4]",
    "LONG/SHORT FUERTE ticker @ $X  RSI  Vol  señales", "#1976D2")

# ── BROKERS ───────────────────────────────────────────────────────────────────
ax.text(RC, 13.8, "BROKERS & EJECUCIÓN", ha="center",
        fontsize=11, fontweight="bold", color="#E65100")

# B1
b1x = RX + 0.3
box(ax, b1x, 12.7, 2.1, 1.8, "B1 — DeGiro\n(EUR)", None, "#BF360C", fontsize=9)
b1_items = ["• Solo LONG", "• 1 orden/activo\n  (SL ó TP, no ambas)", "• €2 + 0.25% AutoFX",
            "• AAPL · INTC · NGAS"]
for i, t in enumerate(b1_items):
    ax.text(b1x + 0.15, 12.55 - i*0.28, t, fontsize=7, color="#3E2723")

# B2
b2x = RX + 2.8
box(ax, b2x, 12.7, 2.1, 1.8, "B2 — ColmexPro\n(USD)", None, "#E65100", fontsize=9)
b2_items = ["• Long + Short", "• Cash (sin margin\n  call real)", "• $2.50/lado",
            "• WULF · HIMS"]
for i, t in enumerate(b2_items):
    ax.text(b2x + 0.15, 12.55 - i*0.28, t, fontsize=7, color="#BF360C")

# B3
b3x = RX + 5.3
box(ax, b3x, 12.7, 2.1, 1.8, "B3 — FPMTrading\n(MT4 CFDs)", None, "#4E342E", fontsize=9)
b3_items = ["• Solo MANUAL", "• Índices, gold,\n  silver, FX", "• 1:30 leverage",
            "• EA activo"]
for i, t in enumerate(b3_items):
    ax.text(b3x + 0.15, 12.55 - i*0.28, t, fontsize=7, color="#3E2723")

# Flechas pipeline → brokers
arrow_h(ax, 8.85, RX + 0.3, 8.5, "#BF360C")
ax.text(9.2, 8.6, "LONG → B1/B2\nSHORT → B2", fontsize=7.5, color="#B71C1C", ha="center")

# ── CONTEXTO compartido ───────────────────────────────────────────────────────
ax.text(RC, 11.5, "CONTEXTO COMPARTIDO", ha="center",
        fontsize=10, fontweight="bold", color="#6A1B9A")

ctx_items = [
    ("portfolio.json",           "Posiciones abiertas · SL/TP/BEP · P&L · seguimiento_lunes"),
    ("watchlist.json",           "107 tickers · universo de análisis"),
    ("daily_state_YYYY-MM-DD",   "Output pipeline · contexto siguiente run"),
    ("output/logs/",             "trading_agent.log · errores · debug"),
]
for i, (k, v) in enumerate(ctx_items):
    y = 11.1 - i * 0.55
    box(ax, RX + 0.3, y, 2.8, 0.42, k, None, "#6A1B9A", fontsize=7.5)
    ax.text(RX + 3.3, y + 0.21, v, va="center", fontsize=7.5, color="#4A148C")

# ── Leyenda ───────────────────────────────────────────────────────────────────
ax.plot([0.2, 17.8], [2.1, 2.1], color="#B0BEC5", lw=1)
legend = [
    ("#1565C0", "Pipeline (Claude AI)"), ("#00838F", "Entry Scanner H4"),
    ("#6A1B9A", "Contexto/Estado"),      ("#2E7D32", "Outputs"),
    ("#E65100", "Brokers"),              ("#37474F", "Filtros/Logic"),
]
for i, (c, lbl) in enumerate(legend):
    bx = 0.4 + i * 2.9
    rect = FancyBboxPatch((bx, 1.55), 0.45, 0.38,
                          boxstyle="round,pad=0.05",
                          facecolor=c, edgecolor="white", linewidth=1, zorder=3)
    ax.add_patch(rect)
    ax.text(bx + 0.55, 1.74, lbl, va="center", fontsize=8, color="#212121")

ax.text(9, 1.1, "Scores: Scan 15%  ·  Fundamental 15%  ·  TA 35%  ·  Sentiment 15%  ·  Risk 20%  |  "
        "STRONG BUY ≥ 7.5  ·  BUY ≥ 6.0  ·  WATCH < 6.0",
        ha="center", va="center", fontsize=8, color="#455A64", style="italic")
ax.text(9, 0.65, "Pipeline: D1 · 90 días historial     |     Entry Scanner: H4 · 45 días historial",
        ha="center", va="center", fontsize=8, color="#455A64", style="italic")

plt.tight_layout(pad=0.3)
plt.savefig(OUTPUT, format="pdf", dpi=150, bbox_inches="tight", facecolor="#FAFAFA")
plt.close()
print(f"OK: {OUTPUT}")
