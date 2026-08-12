from pathlib import Path
from dotenv import load_dotenv
import json
import os

# Verificación TLS contra el almacén de certificados de Windows.
# Avast (Web/Mail Shield) intercepta el HTTPS y lo re-firma con su propia CA;
# esa CA es de confianza en Windows pero no está en el bundle de certifi, así
# que sin esto cualquier verificación normal fallaría. Se hace aquí porque
# config se importa en todos los puntos de entrada antes de cualquier I/O de red,
# y deja TLS verificado de forma limpia para requests y httpx en todo el proyecto.
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

load_dotenv(override=True)

ROOT_DIR = Path(__file__).parent
CONTEXT_DIR = ROOT_DIR / "contex"
OUTPUT_DIR = ROOT_DIR / "output"
LOGS_DIR = ROOT_DIR / "output" / "logs"

# Niveles de modelo por coste (input/output por MTok):
#   Sonnet 4.6  $3 / $15   — fases mecánicas y de alto volumen
#   Opus 4.8    $5 / $25   — decisiones críticas (riesgo, informe final)
#   Fable 5     $10 / $50  — máxima capacidad (no usado por defecto)
MODEL_CHEAP = "claude-sonnet-4-6"
MODEL_PREMIUM = "claude-opus-4-8"

# Modelo global por defecto (fallback para agentes sin override y scripts auxiliares)
ANTHROPIC_MODEL = MODEL_CHEAP
ANTHROPIC_MAX_TOKENS = 4096
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SCAN_MAX_CANDIDATES = 50
FUNDAMENTAL_TOP_N = 35
TA_TOP_N = 15
SENTIMENT_TOP_N = 10
RISK_TOP_N = 10
FINAL_REPORT_N = 4
EARNINGS_BLOCK_DAYS = 3        # earnings inminente: siempre fuera
# Filtro B (2026-07-27): bloquea earnings DENTRO de la ventana de hold. El hold
# estimado es 5-10 sesiones (~14 días naturales); sostener una posición durante el
# reporte es riesgo binario de gap, imposible de gestionar con un stop normal.
# 12 días naturales cubren el grueso del hold sin vaciar el pool en plena temporada
# de resultados. Tunable por el operador.
EARNINGS_HOLD_BLOCK_DAYS = 12

# Watchlist — política de crecimiento con puerta de CALIDAD (2026-07-27).
# La watchlist almacena "buenos tickets" para operar, no nombres que tuvieron un
# buen día. Los screeners de momentum/técnicos (ta_weekly_long, ta_monthly_breakout,
# short_screener) descubren candidatos para analizar HOY pero NO se persisten (son
# efímeros, como los gappers). Solo el screener fundamental de calidad (long_screener:
# analyst Strong Buy + insider comprando) puede PROMOCIONAR a la watchlist, y solo si
# supera este suelo de fundamental_score. El núcleo curado (source: manual) nunca se
# expulsa: la rotación one-in-one-out solo recicla entradas auto-añadidas.
WATCHLIST_PROMOTE_MIN_FUND = 7.0
WATCHLIST_MAX_SIZE = 130

# Filtro de beta en el descubrimiento (2026-07-27). La tesis es momentum de alta
# beta (estrategia_riesgo.md §1): un nombre que apenas se mueve no da recorrido de
# swing aunque el índice se agite (EWS beta 0.53, BANC 0.74). Se filtra en los
# screeners de descubrimiento — NO en la watchlist curada (los Tier A de baja beta
# se añaden a mano). String de Finviz: "Over 1" / "Over 1.5" / "Over 2".
# OJO: la beta no caza nombres laterales de beta normal (BRKR beta 1.28, volátil
# pero sin tendencia) — de eso se encarga el filtro C (ADX). Un string inválido
# tumba la query del screener; usar solo valores verificados con set_filter.
SCREENER_MIN_BETA = "Over 1"

MIN_PRICE = 1.0
MAX_PRICE = 5000.0
MIN_AVG_VOLUME = 500_000
MIN_MARKET_CAP = 1_000_000_000
VOLUME_SPIKE_THRESHOLD = 1.5
NEAR_52W_HIGH_PCT = 0.85

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

# Filtro C (2026-07-27): suelo de fuerza de tendencia para setups de breakout.
# ADX < 20 = sin tendencia establecida → las rupturas son mayormente falsas. Un
# largo/corto etiquetado como breakout con ADX por debajo de este umbral se degrada
# a WATCH (no se opera la ruptura sin inercia real). No afecta a pullbacks/reversiones.
ADX_TREND_MIN = 20

# MA200 multi-timeframe (tendencia mayor). "media de 200 sesiones" = SMA200.
# El TecnicalAnalyst la calcula en semanal/diario/4h y la inyecta en score+prompt.
MA200_PERIOD = 200
MA200_DAILY_PERIOD = "6y"      # ~1500 velas diarias: sirve para MA200 diaria + resample semanal
MA200_4H_PERIOD = "220d"       # suficiente para ≥200 velas de 4h
MA200_CONFLUENCE_MAX = 1.5     # modificador máx (+/-) al ta_score por confluencia de MA200

# Capital real operado por el pipeline = broker_1 (DeGiro) + broker_2 (Colmex),
# leído dinámicamente de los balances en contex/portfolio.json para que se ajuste
# solo cada día. Broker_3 (CFDs, manual) queda fuera. USD~EUR a la par (misma
# simplificación que R1/R2/R6). Fallback a $PORTFOLIO_VALUE/7000 si no hay fichero.
def _compute_portfolio_value() -> float:
    fallback = float(os.environ.get("PORTFOLIO_VALUE", "7000"))
    try:
        pf = json.loads((CONTEXT_DIR / "portfolio.json").read_text(encoding="utf-8"))
        brokers = pf.get("brokers", {})
        b1 = brokers.get("broker_1", {})
        b2 = brokers.get("broker_2", {})
        total = float(b1.get("cuenta_completa_eur") or 0)                       # EUR
        total += float(b2.get("balance_usd") or b2.get("projected_balance_usd") or 0)  # USD~EUR
        return round(total, 2) if total > 0 else fallback
    except Exception:
        return fallback


PORTFOLIO_VALUE = _compute_portfolio_value()
MAX_POSITION_PCT = 0.20
MIN_RR_RATIO = 1.5
ATR_STOP_MULTIPLIER = 2.5

SCORE_WEIGHTS = {"scan": 0.15, "fundamental": 0.15, "ta": 0.35, "sentiment": 0.15, "risk": 0.20}

# ── Política de riesgo/exposición — docs/estrategia_riesgo.md §4 ──────────────
# Wired 2026-06-15. Implementación de las reglas R1/R2/R3 (R4 ya vivía en el
# orchestrator). Lógica y clasificación de tiers en utils/risk_policy.py.

# R1 — Cap de exposición a alta beta (Tier B+C) por régimen, sobre PORTFOLIO_VALUE
HIGH_BETA_CAP_STRONG_UP = 0.80   # Strong Uptrend / VIX < 18
HIGH_BETA_CAP_UPTREND   = 0.70   # Uptrend / VIX 18-22
HIGH_BETA_CAP_NEUTRAL   = 0.60   # NEUTRAL/Sideways / VIX >= 20
HIGH_BETA_CAP_RISKOFF   = 0.40   # Downtrend/risk-off / VIX > 25

# R2 — Concentración máx. de un sub-tema de Tier C dentro del presupuesto de alta beta
SUBTHEME_MAX_PCT = 0.40

# R3 (redefinida 2026-06-24) — Perfil de riesgo dinámico por trade, INVERSO al VIX.
# Reemplaza al antiguo half-size fijo. El riesgo objetivo por operación (% del
# PORTFOLIO_VALUE) sube en calma y baja en estrés. Mismos buckets de régimen que R1.
#   shares = (RISK_PCT × PORTFOLIO_VALUE) / (entrada − stop), capado por MAX_POSITION_PCT.
RISK_PCT_STRONG_UP = 0.03   # Strong Uptrend / VIX < 18  → €210 sobre 7k
RISK_PCT_UPTREND   = 0.02   # Uptrend / VIX 18-22        → €140
RISK_PCT_NEUTRAL   = 0.015  # NEUTRAL/Sideways / VIX>=20 → €105
RISK_PCT_RISKOFF   = 0.01   # Downtrend/risk-off / VIX>25 → €70

# Banda de inversión bruta por operación (decisión del operador). El motor de
# riesgo (RISK_PCT) propone el tamaño y se acota a esta banda. La INVERSIÓN MANDA
# a nivel de trade: el suelo se respeta con acciones enteras aunque eleve el riesgo
# por encima del % del régimen (p.ej. acción de €400 → mínimo 2 acciones = €800);
# el tope agregado de cartera (R6) sigue siendo el límite duro. Capado además por
# MAX_POSITION_PCT como techo absoluto de capital.
MIN_INVEST_PER_TRADE = 500.0
MAX_INVEST_PER_TRADE = 800.0

# R6 (nueva 2026-06-24) — Tope de riesgo agregado de cartera. La suma del riesgo
# abierto (posiciones existentes + nuevas) no puede superar este % del capital.
# Es el verdadero freno al clúster correlacionado (la lección del 5-jun: VIX bajo
# NO protege de un giro de factor). Aplicado en orchestrator._apply_exposure_caps.
PORTFOLIO_RISK_CAP_PCT = 0.10   # €700 sobre 7k

# Stop por defecto para una posición SIN stop colocado, al calcular el riesgo
# agregado de R6 (opción del operador: usar el stop definido/sugerido; si no hay,
# asumir este % por debajo del precio). Fuerza a contar el riesgo real, no a ignorarlo.
DEFAULT_STOP_PCT = 0.08

# R7 (nueva 2026-07-14) — Enfriamiento de re-entrada tras pérdida reciente.
# El análisis de calidad de selección (jul-2026, 3 semanas) mostró que el motor
# re-recomienda una y otra vez nombres que acaban de stopear (GRAB ×3, MRVL, INTC:
# todos perdedores), sin memoria de que el nombre acaba de fallar. Si un ticker
# cerró en PÉRDIDA en los últimos N días de calendario, no se re-recomienda en la
# MISMA dirección (queda WATCH-only): deja que el setup "resetee" antes de reintentar.
# Un cierre perdedor por debajo de este umbral (scratch) NO dispara el enfriamiento.
# Aplicado en orchestrator._apply_recent_loss_cooldown (utils.risk_policy.recent_loss_cooldown).
RECENT_LOSS_COOLDOWN_DAYS = 5
RECENT_LOSS_MIN_ABS = 10.0   # pérdida neta mín. (€/$ abs.) para contar como "stop real"

# R8 (nueva 2026-07-30) — Guarda de catalizador de sentiment alcista en cortos.
# Caso BE (29/07/2026): se abrió un corto un día después de un earnings-beat con
# guidance al alza (sentiment_score_normalized 8.9, catalyst_found=True); el propio
# resumen del risk_manager avisaba en texto de "riesgo de short squeeze", pero nada
# lo convertía en veto — el corto se mantuvo y el squeeze (+25% en 24h) forzó el stop
# con fuerte slippage. Si el sentiment analyst encuentra un catalizador reciente Y
# el sentiment normalizado supera este umbral, el corto se degrada a WATCH sin más
# cálculo de score (igual de duro que el resto de R4). Aplicado en
# orchestrator._merge_and_rank (utils.risk_policy.short_bullish_catalyst_guard).
SHORT_BULLISH_CATALYST_MIN = 7.5

# R9 (nueva 2026-08-04; sensible a la hora del evento desde 2026-08-12) — Guarda de
# evento macro de alto impacto (calendario económico). Fuente: feed público de Forex
# Factory (nfs.faireconomy.media/ff_calendar_thisweek.json), sin API key — ver
# data/economic_calendar.py. Un evento de alto impacto (FOMC, CPI, NFP...) el mismo
# día Y AÚN PENDIENTE de publicarse puede ser catalizador o cisne negro en cualquier
# dirección: ante la incertidumbre no se abren NUEVAS posiciones mientras siga
# pendiente (se degradan a WATCH); un evento del mismo día ya publicado no cuenta
# (get_high_impact_events() filtra por hora, no solo por fecha). No toca la cartera
# existente. Aplicado en orchestrator._apply_macro_event_guard
# (utils.risk_policy.macro_event_guard).
ECONOMIC_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
ECONOMIC_CALENDAR_CACHE_TTL_HOURS = 6   # FF limita a 2 descargas/5min del fichero semanal
MACRO_EVENT_COUNTRIES = ["USD"]         # watchlist mayoritariamente US; broker_2 solo NYSE/NASDAQ
MACRO_EVENT_MIN_IMPACT = "High"

# ── Seguimiento (vigilancia de extendidos para entrada en pullback) ──────────
# La lista era append-only y acumulaba tickers con precios de referencia de hace
# semanas ("esperar pullback a $478" cuando ya no significa nada). Una entrada
# caduca a los N días; también se purga en cuanto el ticker entra en cartera,
# porque entonces la vigilancia ya cumplió su función.
# Aplicado en market_scanner._track_extended_tickers.
SEGUIMIENTO_MAX_AGE_DAYS = 30

# ── R5 — Calidad de entrada ("exigir fuerza, no comprar debilidad") ──────────
# Calibrado con backtest_entry_quality.py (83 recs largas, 06/05-15/06):
# lo que abre en rojo y salta por stop NO es la extensión sino la DEBILIDAD —
# comprar con el precio flojo/por debajo de la EMA9 (cuchillo cayendo):
#   <=0.5 ATR sobre EMA9 -> abre rojo 58%, stop 62%, ret+5d +1.0%
#    >0.5 ATR sobre EMA9 -> abre rojo 27%, stop 36%, ret+5d +8.1%
# Un largo es "débil" si cotiza < WEAK_ENTRY_ATR_MIN ATR sobre la EMA9: no entrar
# a mercado, exigir recuperación de fuerza sobre la EMA9. Aplicado en risk_manager.
WEAK_ENTRY_ATR_MIN = 0.5

# ── R4 — Guardia de entrada para cortos (simétrico a R5) ─────────────────────
# R4 entra el corto en el pullback a la EMA9. Pero cuando el precio se ha
# desplomado MUY por debajo de la EMA9 (downtrend agudo), ese rebote-entrada
# queda irreal (p.ej. RKLB 2026-06-25: precio 80.85, entrada en EMA9 97 = +20%):
# no se llenaría e infla el stop/riesgo. Igual que R5 evita "comprar debilidad"
# en largos, esto evita "cortar un valor ya desplomado": si la distancia
# precio↔EMA9 supera SHORT_EXTENDED_ATR_MAX ATR, el corto es WATCH-only (no
# genera SELL accionable con fill fantasma; se mantiene la entrada en EMA9 como
# referencia y se anota el motivo). Aplicado en risk_manager + orchestrator.
SHORT_EXTENDED_ATR_MAX = 1.5

BROKER2_COMMISSION = 5.00  # $2.50 entrada + $2.50 salida = $5.00 ida+vuelta

RUN_TIME = os.environ.get("RUN_TIME", "15:00")
RUN_TIME_EVENING = os.environ.get("RUN_TIME_EVENING", "20:30")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

WEBHOOK_PORT = int(os.environ.get("WEBHOOK_PORT", "5000"))
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.environ.get("ALPACA_API_SECRET", "")

US_MARKET_HOLIDAYS_2026 = [
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
    "2026-05-25", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
]
