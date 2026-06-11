"""Genera el PDF de presentacion del proyecto trading-agent."""
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from datetime import date

OUTPUT = "output/trading_agent_presentacion.pdf"

# ── Colores corporativos ─────────────────────────────────────────────────────
DARK   = colors.HexColor("#0d1117")
ACCENT = colors.HexColor("#238636")
BLUE   = colors.HexColor("#1f6feb")
LIGHT  = colors.HexColor("#f0f6fc")
GRAY   = colors.HexColor("#8b949e")
WARN   = colors.HexColor("#d29922")

# ── Estilos ──────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

def S(name, **kw):
    return ParagraphStyle(name, **kw)

TITLE_STYLE = S("title",
    fontSize=28, leading=34, textColor=DARK,
    alignment=TA_CENTER, fontName="Helvetica-Bold", spaceAfter=6)

SUBTITLE_STYLE = S("subtitle",
    fontSize=13, leading=18, textColor=GRAY,
    alignment=TA_CENTER, fontName="Helvetica", spaceAfter=4)

H1 = S("h1",
    fontSize=16, leading=20, textColor=ACCENT,
    fontName="Helvetica-Bold", spaceBefore=18, spaceAfter=6,
    borderPadding=(0, 0, 4, 0))

H2 = S("h2",
    fontSize=12, leading=16, textColor=BLUE,
    fontName="Helvetica-Bold", spaceBefore=12, spaceAfter=4)

BODY = S("body",
    fontSize=10, leading=15, textColor=DARK,
    fontName="Helvetica", alignment=TA_JUSTIFY, spaceAfter=6)

MONO = S("mono",
    fontSize=9, leading=13, textColor=colors.HexColor("#24292f"),
    fontName="Courier", backColor=colors.HexColor("#f6f8fa"),
    borderPadding=8, spaceAfter=6)

BULLET = S("bullet",
    fontSize=10, leading=15, textColor=DARK,
    fontName="Helvetica", leftIndent=16, spaceAfter=3,
    bulletIndent=6)

SMALL = S("small",
    fontSize=8.5, leading=12, textColor=GRAY,
    fontName="Helvetica", alignment=TA_CENTER)

TAG_NEW = S("tag",
    fontSize=8, leading=10, textColor=colors.white,
    fontName="Helvetica-Bold", backColor=ACCENT,
    borderPadding=(2, 6, 2, 6), alignment=TA_CENTER)

# ── Helpers ──────────────────────────────────────────────────────────────────
def HR():
    return HRFlowable(width="100%", thickness=1, color=colors.HexColor("#e1e4e8"), spaceAfter=8, spaceBefore=4)

def sp(h=6):
    return Spacer(1, h)

def P(text, style=None):
    return Paragraph(text, style or BODY)

def bullet(text):
    return Paragraph(f"<bullet>&bull;</bullet> {text}", BULLET)

def badge(text, bg=ACCENT):
    style = ParagraphStyle("badge_", fontSize=8, fontName="Helvetica-Bold",
                           textColor=colors.white, backColor=bg,
                           borderPadding=(2, 4, 2, 4))
    return Paragraph(text, style)

# ── Tabla de agentes ─────────────────────────────────────────────────────────
def agents_table():
    header_style = ParagraphStyle("th", fontSize=9, fontName="Helvetica-Bold",
                                  textColor=colors.white)
    cell_style   = ParagraphStyle("td", fontSize=9, fontName="Helvetica",
                                  textColor=DARK, leading=13)
    mono_cell    = ParagraphStyle("tdm", fontSize=8, fontName="Courier",
                                  textColor=colors.HexColor("#0550ae"), leading=12)

    data = [
        [P("Agente", header_style), P("Modelo", header_style),
         P("Funcion principal", header_style), P("Peso score", header_style)],
        [P("MarketScanner", mono_cell),   P("Sonnet 4.6", cell_style),
         P("Scan universe, filtros momentum, sector balance", cell_style),  P("15%", cell_style)],
        [P("FundamentalAnalyst", mono_cell), P("Sonnet 4.6", cell_style),
         P("Filtrado earnings, Finviz, 4 screeners discovery", cell_style), P("15%", cell_style)],
        [P("TechnicalAnalyst", mono_cell),  P("Sonnet 4.6", cell_style),
         P("90d OHLCV, indicadores Python + patron Claude", cell_style),    P("35%", cell_style)],
        [P("NewsSentimentAnalyst", mono_cell), P("Sonnet 4.6", cell_style),
         P("Sentiment noticias recientes por ticker", cell_style),          P("15%", cell_style)],
        [P("RiskManager", mono_cell),  P("Opus 4.8", cell_style),
         P("Sizing, SL via ATR, dos TPs, ratio R/R minimo", cell_style),    P("20%", cell_style)],
        [P("ReportWriter", mono_cell), P("Opus 4.8", cell_style),
         P("Sintesis final, ranking, parametros completos", cell_style),    P("—", cell_style)],
    ]

    t = Table(data, colWidths=[4.2*cm, 2.5*cm, 8.5*cm, 2.0*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), DARK),
        ("TEXTCOLOR",    (0, 0), (-1, 0), colors.white),
        ("GRID",         (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ("BACKGROUND",   (0, 5), (-1, 6), colors.HexColor("#fff8e6")),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",   (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 6),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t

# ── Tabla screeners ──────────────────────────────────────────────────────────
def screeners_table():
    cs = ParagraphStyle("td", fontSize=9, fontName="Helvetica", textColor=DARK, leading=13)
    hs = ParagraphStyle("th", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)
    ms = ParagraphStyle("mo", fontSize=8, fontName="Courier",
                        textColor=colors.HexColor("#0550ae"), leading=12)

    data = [
        [P("Screener", hs), P("Filtros clave", hs), P("Limite", hs), P("Source tag", hs)],
        [P("Fundamental Long",  cs),
         P("Strong Buy + insider positivo + vol>500K", cs), P("8", cs), P("long_screener", ms)],
        [P("Fundamental Short", cs),
         P("Float short >10% + semana bajista + vol>500K", cs), P("5", cs), P("short_screener", ms)],
        [P("TA Semanal Long",   cs),
         P("Precio > SMA20/50/200, semana+mes en verde, RSI no sobrecomprado", cs), P("10", cs), P("ta_weekly_long", ms)],
        [P("TA Mensual Breakout", cs),
         P("0-10% bajo maximo 52W, > SMA200, trimestre+semestre en verde", cs), P("8", cs), P("ta_monthly_breakout", ms)],
    ]

    t = Table(data, colWidths=[3.8*cm, 8.2*cm, 1.8*cm, 3.4*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), BLUE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ("BACKGROUND",    (0, 3), (-1, 4), colors.HexColor("#e6f4ea")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    return t

# ── Tabla fuentes de datos ───────────────────────────────────────────────────
def datasources_table():
    cs = ParagraphStyle("td", fontSize=9, fontName="Helvetica", textColor=DARK, leading=13)
    hs = ParagraphStyle("th", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)

    data = [
        [P("Fuente", hs), P("Uso", hs), P("Rol", hs)],
        [P("Alpaca Markets API", cs), P("Cotizaciones RT, OHLCV historico", cs), P("Primaria", cs)],
        [P("yfinance", cs),           P("Fallback general + ^VIX exclusivo", cs), P("Secundaria", cs)],
        [P("Finviz", cs),             P("Fundamentales + screener discovery", cs), P("Discovery", cs)],
        [P("TradingView Desktop", cs),P("Visualizacion + alertas via MCP", cs),   P("Auxiliar", cs)],
        [P("Telegram Bot API", cs),   P("Entrega de alertas y reportes", cs),      P("Output", cs)],
    ]

    t = Table(data, colWidths=[4.5*cm, 8.5*cm, 4.2*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#6e40c9")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    return t

# ── Score thresholds ─────────────────────────────────────────────────────────
def score_table():
    cs = ParagraphStyle("td", fontSize=10, fontName="Helvetica", textColor=DARK, leading=14)
    hs = ParagraphStyle("th", fontSize=10, fontName="Helvetica-Bold", textColor=colors.white)

    data = [
        [P("composite_score", hs), P("Recomendacion", hs)],
        [P(">= 7.5", cs), P("STRONG BUY", cs)],
        [P(">= 6.0", cs), P("BUY", cs)],
        [P("< 6.0",  cs), P("WATCH", cs)],
    ]

    t = Table(data, colWidths=[5*cm, 5*cm])
    t.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), DARK),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("BACKGROUND",    (0, 1), (-1, 1), colors.HexColor("#e6f4ea")),
        ("BACKGROUND",    (0, 2), (-1, 2), colors.HexColor("#fff8e6")),
        ("BACKGROUND",    (0, 3), (-1, 3), colors.HexColor("#f6f8fa")),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t

# ── BUILD ────────────────────────────────────────────────────────────────────
def build():
    import os
    os.makedirs("output", exist_ok=True)

    doc = SimpleDocTemplate(
        OUTPUT, pagesize=A4,
        leftMargin=2*cm, rightMargin=2*cm,
        topMargin=2.2*cm, bottomMargin=2*cm,
        title="Trading Agent — Presentacion del Proyecto",
        author="bucki",
    )

    story = []

    # ── PORTADA ──────────────────────────────────────────────────────────────
    story += [sp(40)]
    story += [P("Trading Agent", TITLE_STYLE)]
    story += [P("Sistema de Swing Trading con Pipeline Multi-Agente de IA", SUBTITLE_STYLE)]
    story += [sp(8)]
    story += [HR()]
    story += [sp(4)]
    story += [P(f"Junio 2026  ·  Python + Anthropic Claude + Alpaca + Finviz", SMALL)]
    story += [sp(60)]

    # resumen ejecutivo en caja
    summary_style = ParagraphStyle("summary", fontSize=10, leading=16,
                                   textColor=DARK, fontName="Helvetica",
                                   backColor=colors.HexColor("#f0f6fc"),
                                   borderColor=BLUE, borderWidth=1,
                                   borderPadding=14, alignment=TA_JUSTIFY)
    story += [P(
        "Sistema automatizado de swing trading que ejecuta un pipeline diario de seis agentes de IA "
        "para analizar un universo de ~103 tickers. Cada agente enriquece la informacion del anterior "
        "y genera una puntuacion compuesta que determina recomendaciones de entrada con parametros "
        "de riesgo completos (entrada, SL, TP1, TP2). Los resultados se entregan via reportes "
        "JSON/TXT y notificaciones Telegram.",
        summary_style)]
    story += [sp(20)]
    story += [P(f"Documento preparado el {date.today().strftime('%d de %B de %Y')}  ·  Confidencial", SMALL)]

    story += [PageBreak()]

    # ── 1. ARQUITECTURA GENERAL ───────────────────────────────────────────────
    story += [P("1. Arquitectura General", H1)]
    story += [HR()]
    story += [P(
        "El pipeline se ejecuta secuencialmente dos veces al dia (manana y tarde) coordinado por "
        "<b>TradingOrchestrator</b> en <i>orchestrator.py</i>. Cada fase esta envuelta en "
        "try/except — un fallo en cualquier agente intermedio no aborta el pipeline. "
        "La unica excepcion es el scan inicial: si MarketScanner falla, no hay candidatos "
        "y el pipeline termina.", BODY)]
    story += [sp(8)]

    # diagrama ASCII
    story += [P("""MarketScanner
      |
      v  (hasta 50 candidatos + puntuacion inicial)
FundamentalAnalyst
      |
      v  (filtrado earnings, score fundamental, discovery)
TechnicalAnalyst
      |
      v  (indicadores Python + patron Claude, ta_score)
NewsSentimentAnalyst
      |
      v  (sentiment noticias, sentiment_score)
RiskManager                  [Opus 4.8]
      |
      v  (sizing, SL/TP, ratio R/R)
ReportWriter                 [Opus 4.8]
      |
      v
output/report_YYYY-MM-DD.{txt,json}  +  Telegram""", MONO)]

    story += [sp(10)]
    story += [P("Puntuacion compuesta final", H2)]
    story += [P(
        "Cada agente contribuye con un porcentaje al <b>composite_score</b> (0-10) "
        "que determina la recomendacion final:", BODY)]
    story += [sp(4)]

    score_weights = [
        ["MarketScanner", "15%", "Momentum, volumen, posicion tecnica inicial"],
        ["FundamentalAnalyst", "15%", "Calidad fundamental, insiders, analistass"],
        ["TechnicalAnalyst", "35%", "Indicadores + patrones — mayor peso del pipeline"],
        ["NewsSentimentAnalyst", "15%", "Sentiment de noticias recientes"],
        ["RiskManager", "20%", "Calidad del setup riesgo/recompensa"],
    ]
    ws = ParagraphStyle("wt", fontSize=9, fontName="Helvetica", textColor=DARK, leading=13)
    wh = ParagraphStyle("wh", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)
    wdata = [[P("Agente", wh), P("Peso", wh), P("Que mide", wh)]] + \
            [[P(r[0], ws), P(r[1], ws), P(r[2], ws)] for r in score_weights]
    wt = Table(wdata, colWidths=[4.5*cm, 2*cm, 10.7*cm])
    wt.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), DARK),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    story += [wt]
    story += [sp(10)]
    story += [P("Umbrales de recomendacion", H2)]
    story += [score_table()]

    story += [PageBreak()]

    # ── 2. AGENTES ────────────────────────────────────────────────────────────
    story += [P("2. Agentes del Pipeline", H1)]
    story += [HR()]
    story += [agents_table()]
    story += [sp(8)]
    story += [P(
        "<i>Nota sobre modelos:</i> Los agentes de razonamiento complejo (RiskManager y ReportWriter) "
        "usan <b>Claude Opus 4.8</b> para maxima calidad en las decisiones criticas. "
        "Los cuatro agentes de analisis y filtrado usan <b>Claude Sonnet 4.6</b>, "
        "consiguiendo un ahorro estimado del <b>65-70%</b> en costes de API "
        "sin degradar la calidad del output final.", BODY)]

    story += [sp(14)]

    # Descripcion de cada agente
    agents_detail = [
        ("MarketScanner", [
            "Descarga cotizaciones del universo completo en batches de 50 via yfinance.",
            "Aplica filtros de precio (>5$) y volumen medio (>500K).",
            "Puntua candidatos por senales momentum: cambio intradía, distancia a maximo 52W, ratio de volumen.",
            "Pide a Claude priorizar los top 30 considerando la exposicion sectorial del portfolio activo.",
            "Detecta tickers extendidos intradía (movimiento >= 5%) y los guarda en una watchlist de "
            "seguimiento (portfolio.json > seguimiento) para detectar pullbacks a EMA9/EMA21 en sesiones posteriores.",
        ]),
        ("FundamentalAnalyst", [
            "Obtiene de Finviz: fecha earnings, short float, recomendaciones analistas, "
            "transacciones insider e institucionales, precio objetivo, apalancamiento.",
            "Bloquea automaticamente cualquier ticker con earnings en los proximos 3 dias (riesgo gap).",
            "Puntua 0-10 con cache de 7 dias para evitar llamadas repetidas.",
            "Ejecuta 4 screeners de descubrimiento para incorporar tickers nuevos a la watchlist "
            "(ver seccion 3 para detalle completo).",
        ]),
        ("TechnicalAnalyst", [
            "Descarga 90 dias de OHLCV por ticker (Alpaca primario, yfinance fallback).",
            "Calcula en Python: RSI, MACD, EMA9/21/50/200, Bollinger Bands, ATR, ADX, "
            "niveles de soporte y resistencia.",
            "Envia los indicadores a Claude para deteccion de patrones y asignacion de ta_score.",
            "Long: composite = promedio(score Python, score Claude). Short: usa score Claude directamente.",
        ]),
        ("NewsSentimentAnalyst", [
            "Busca noticias recientes de cada candidato superviviente.",
            "Claude evalua el sentiment (positivo / negativo / neutro) y lo normaliza a 0-10.",
        ]),
        ("RiskManager  [Opus 4.8]", [
            "Calcula el tamano de posicion basado en riesgo fijo en dolares por trade (configurado en .env).",
            "Determina el Stop Loss usando un multiplicador del ATR(14).",
            "Calcula dos Targets de Beneficio (TP1 conservador, TP2 agresivo).",
            "Descarta candidatos que no alcanzan el ratio minimo riesgo/recompensa (MIN_RR_RATIO).",
        ]),
        ("ReportWriter  [Opus 4.8]", [
            "Sintetiza todos los resultados del pipeline en un reporte final.",
            "Rankea candidatos por composite_score y clasifica en STRONG BUY / BUY / WATCH.",
            "Genera output en texto legible (.txt) y estructurado (.json) en la carpeta output/.",
            "Envia resumen y alertas prioritarias por Telegram si esta configurado.",
        ]),
    ]

    for name, bullets in agents_detail:
        story += [P(name, H2)]
        for b in bullets:
            story += [bullet(b)]
        story += [sp(4)]

    story += [PageBreak()]

    # ── 3. WATCHLIST DINAMICA ─────────────────────────────────────────────────
    story += [P("3. Gestion Dinamica de la Watchlist", H1)]
    story += [HR()]
    story += [P(
        "La watchlist es el universo de tickers que el pipeline analiza cada dia. "
        "Para mantenerla siempre actualizada y relevante se implemento un sistema "
        "<b>one-in one-out</b>: por cada ticker nuevo que descubren los screeners, "
        "se elimina automaticamente el mas antiguo del pool.", BODY)]

    story += [sp(8)]
    story += [P("3.1  Formato del archivo watchlist.json", H2)]
    story += [P("""// watchlist.json — nuevo formato
{
  "updated": "2026-06-11",
  "entries": [
    { "ticker": "NVDA", "added": "2026-05-27", "source": "manual"            },
    { "ticker": "XYZ",  "added": "2026-06-11", "source": "ta_weekly_long"    },
    ...
  ],
  "tickers": ["NVDA", "XYZ", ...]   // derivado de entries (compatibilidad)
}""", MONO)]

    story += [P(
        "El campo <b>source</b> registra el origen de cada ticker para trazabilidad, "
        "pero <b>no protege ninguna entrada</b>: tickers manuales y de screener "
        "compiten en igualdad de condiciones. Si un ticker lleva mucho tiempo en "
        "la lista sin ser candidato activo, el sistema lo reemplaza.", BODY)]

    story += [sp(8)]
    story += [P("3.2  Logica one-in one-out", H2)]

    flow_style = ParagraphStyle("flow", fontSize=9, fontName="Courier",
                                textColor=DARK, leading=14,
                                backColor=colors.HexColor("#f6f8fa"),
                                borderPadding=10)
    story += [P("""Cada ~7 dias (cadencia de la cache de Finviz):

  Screener descubre N tickers nuevos
        |
        v
  Se anaden al pool (entries)
        |
        v
  Se eliminan los N mas antiguos por fecha (FIFO)
        |
        v
  Tamano del pool permanece estable (~103 tickers)
  tickers[] se regenera como lista plana de entries""", flow_style)]

    story += [sp(10)]
    story += [P("3.3  Los cuatro screeners de descubrimiento", H2)]
    story += [P(
        "El FundamentalAnalyst ejecuta cuatro screeners distintos de Finviz "
        "para cubrir tanto el angulo fundamental como el tecnico en marcos temporales amplios:", BODY)]
    story += [sp(6)]
    story += [screeners_table()]
    story += [sp(8)]
    story += [P(
        "Los screeners TA semanal y mensual son una incorporacion reciente que amplia el "
        "criterio de descubrimiento mas alla del analisis fundamental, buscando tickers "
        "con estructura tecnica solida en marcos temporales mayores antes de que "
        "el pipeline diario los analice en detalle.", BODY)]

    story += [PageBreak()]

    # ── 4. NIVELES DEL SISTEMA ────────────────────────────────────────────────
    story += [P("4. Niveles de Analisis del Sistema", H1)]
    story += [HR()]
    story += [P(
        "El sistema opera en tres niveles temporales complementarios, "
        "cada uno con un objetivo diferente:", BODY)]
    story += [sp(8)]

    levels = [
        ("Nivel 1 — Discovery (semanal/mensual)",
         ACCENT,
         "Finviz screeners (4 tipos)",
         "Poblar la watchlist con nuevos candidatos con fundamentos solidos "
         "o estructura tecnica relevante en marcos temporales amplios. "
         "Cadencia: ~1 vez por semana (cache 7 dias)."),
        ("Nivel 2 — Pipeline diario (timeframe diario)",
         BLUE,
         "Pipeline 6 agentes",
         "Analisis profundo de los ~103 tickers de la watchlist: "
         "fundamental, tecnico (90d OHLCV), sentiment, riesgo. "
         "Genera el ranking diario de oportunidades con parametros de entrada completos. "
         "Cadencia: 2 veces al dia (15:35 y 20:30 CET)."),
        ("Nivel 3 — Scanner intradiario (4H)",
         WARN,
         "EntryScanner",
         "Detecta senales de entrada en el dia sobre los candidatos priorizados "
         "por el pipeline diario. Opera en velas de 4 horas para identificar el "
         "momento optimo de entrada. Genera alertas inmediatas via Telegram."),
    ]

    for title, color, tool, desc in levels:
        level_style = ParagraphStyle("lv", fontSize=11, fontName="Helvetica-Bold",
                                     textColor=color, spaceBefore=10, spaceAfter=4)
        tool_style  = ParagraphStyle("lt", fontSize=9, fontName="Courier",
                                     textColor=colors.HexColor("#0550ae"),
                                     backColor=colors.HexColor("#ddf4ff"),
                                     borderPadding=(2, 6, 2, 6))
        story += [P(title, level_style)]
        story += [P(f"Herramienta: {tool}", tool_style)]
        story += [sp(4)]
        story += [P(desc, BODY)]
        story += [sp(2)]

    story += [sp(10)]
    story += [P("""Discovery (Finviz, semanal)
  Watchlist ~103 tickers
        |
        v
Pipeline diario (6 agentes Claude, timeframe diario)
  Ranking de oportunidades + parametros riesgo
        |
        v
EntryScanner (4H, intradiario)
  Senal de entrada + alerta Telegram
        |
        v
Ejecucion manual en broker""", MONO)]

    story += [PageBreak()]

    # ── 5. FUENTES DE DATOS Y BROKERS ─────────────────────────────────────────
    story += [P("5. Fuentes de Datos", H1)]
    story += [HR()]
    story += [datasources_table()]
    story += [sp(14)]

    story += [P("6. Brokers Configurados", H1)]
    story += [HR()]
    story += [P(
        "El pipeline genera recomendaciones diferenciadas segun las capacidades de cada broker. "
        "La ejecucion de ordenes es siempre <b>manual</b>.", BODY)]
    story += [sp(6)]

    brokers = [
        ("Broker 1", "EUR", "Solo largo", "Mercados europeos + US",
         "Recomendaciones LONG unicamente"),
        ("Broker 2", "USD", "Largo + Corto", "NYSE / NASDAQ",
         "Recomendaciones LONG y SHORT"),
        ("Broker 3 — FPMTrading", "USD", "Manual (CFDs)", "Oro, plata, indices",
         "Operado manualmente. El pipeline NO genera recomendaciones para este broker"),
    ]
    bs = ParagraphStyle("bt", fontSize=9, fontName="Helvetica", textColor=DARK, leading=13)
    bh = ParagraphStyle("bh", fontSize=9, fontName="Helvetica-Bold", textColor=colors.white)
    bdata = [[P("Broker", bh), P("Divisa", bh), P("Operativa", bh),
              P("Mercados", bh), P("Observaciones", bh)]] + \
            [[P(r[0], bs), P(r[1], bs), P(r[2], bs), P(r[3], bs), P(r[4], bs)]
             for r in brokers]
    bt = Table(bdata, colWidths=[3.8*cm, 1.8*cm, 2.8*cm, 3.5*cm, 5.3*cm])
    bt.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), colors.HexColor("#6e40c9")),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ("BACKGROUND",    (0, 3), (-1, 3), colors.HexColor("#fff8e6")),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING",   (0, 0), (-1, -1), 8),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 8),
    ]))
    story += [bt]

    story += [PageBreak()]

    # ── 7. STACK TECNICO ──────────────────────────────────────────────────────
    story += [P("7. Stack Tecnologico", H1)]
    story += [HR()]

    stack = [
        ("Lenguaje", "Python 3.11+"),
        ("IA / LLM", "Anthropic API — Claude Sonnet 4.6 (analisis) + Claude Opus 4.8 (riesgo/reporte)"),
        ("Datos mercado", "Alpaca Markets SDK + yfinance"),
        ("Fundamentales", "finvizfinance (scraping Finviz)"),
        ("Indicadores", "pandas + numpy (RSI, MACD, EMA, BB, ATR, ADX, S/R)"),
        ("Notificaciones", "Telegram Bot API"),
        ("Servidor alertas", "Flask (webhook TradingView) + ngrok (exposicion publica)"),
        ("Visualizacion", "TradingView Desktop con integracion MCP via Chrome DevTools Protocol"),
        ("Automatizacion", "Windows Task Scheduler (15:35 + 20:30 CET, dias laborables)"),
        ("Persistencia", "JSON atomico (write .tmp + os.replace) en carpeta contex/"),
    ]

    ss = ParagraphStyle("st", fontSize=9, fontName="Helvetica", textColor=DARK, leading=13)
    sh = ParagraphStyle("sh", fontSize=9, fontName="Helvetica-Bold", textColor=DARK)
    sm = ParagraphStyle("sm", fontSize=9, fontName="Courier",
                        textColor=colors.HexColor("#0550ae"), leading=13)
    sdata = [[P(r[0], sh), P(r[1], ss)] for r in stack]
    st = Table(sdata, colWidths=[4.5*cm, 12.7*cm])
    st.setStyle(TableStyle([
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#e1e4e8")),
        ("ROWBACKGROUNDS",(0, 0), (-1, -1), [colors.white, colors.HexColor("#f6f8fa")]),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ("LEFTPADDING",   (0, 0), (-1, -1), 10),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 10),
    ]))
    story += [st]

    story += [sp(16)]
    story += [P("Estructura de archivos clave", H2)]
    story += [P("""trading-agent/
  agents/
    orchestrator.py       # Coordina el pipeline completo
    market_scanner.py     # Agente 1 — scan y filtro inicial
    fundamental_analyst.py # Agente 2 — fundamentales + screeners
    technical_analyst.py  # Agente 3 — indicadores + patrones
    risk_manager.py       # Agente 5 — sizing y SL/TP  [Opus 4.8]
    report_writer.py      # Agente 6 — reporte final    [Opus 4.8]
    base_agent.py         # Clase base: retry, cache, modelo por agente
  data/
    market_data.py        # MarketDataFetcher (Alpaca + yfinance)
    indicators.py         # RSI, MACD, EMA, BB, ATR, ADX
  contex/
    watchlist.json        # Universo de tickers con sistema one-in-one-out
    portfolio.json        # Posiciones + watchlist de seguimiento
    daily_state_*.json    # Estado persistido entre runs
  output/
    report_YYYY-MM-DD.txt/.json
    logs/trading_agent.log
  config.py               # Variables globales, pesos, modelos por agente
  main.py                 # CLI principal""", MONO)]

    # ── PIE ───────────────────────────────────────────────────────────────────
    story += [sp(20)]
    story += [HR()]
    story += [P(
        f"Trading Agent  ·  Proyecto personal  ·  {date.today().strftime('%B %Y')}  ·  "
        "Stack: Python + Anthropic Claude + Alpaca + Finviz + TradingView",
        SMALL)]

    doc.build(story)
    print(f"PDF generado: {OUTPUT}")

if __name__ == "__main__":
    build()
