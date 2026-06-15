from pathlib import Path
from dotenv import load_dotenv
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
EARNINGS_BLOCK_DAYS = 3

MIN_PRICE = 1.0
MAX_PRICE = 5000.0
MIN_AVG_VOLUME = 500_000
MIN_MARKET_CAP = 1_000_000_000
VOLUME_SPIKE_THRESHOLD = 1.5
NEAR_52W_HIGH_PCT = 0.85

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70

PORTFOLIO_VALUE = float(os.environ.get("PORTFOLIO_VALUE", "10000"))
MAX_POSITION_PCT = 0.20
MIN_RR_RATIO = 1.5
ATR_STOP_MULTIPLIER = 2.5

# Riesgo fijo en dólares por operación (pérdida máxima si se toca el stop)
MIN_RISK_PER_TRADE = float(os.environ.get("MIN_RISK_PER_TRADE", "500"))
MAX_RISK_PER_TRADE = float(os.environ.get("MAX_RISK_PER_TRADE", "600"))

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

# R3 — Half-size de nuevos longs de Tier C cuando NEUTRAL/Sideways y VIX >= umbral
NEUTRAL_VIX_THRESHOLD = 20.0
HALF_SIZE_FACTOR = 0.50

# ── R5 — Calidad de entrada ("pullback por tier") — estrategia_riesgo §3 ─────
# Un largo está "extendido" si cotiza > ENTRY_EXTENSION_ATR_MAX ATR sobre la EMA9.
# Extendido => no entrar a mercado: Tier C entra en pullback a EMA9/soporte;
# Tier B exige confirmación (cierre sobre el nivel) y entrada en el retest.
# Aplicado en agents/risk_manager.py. Tests en tests/test_entry_quality.py.
ENTRY_EXTENSION_ATR_MAX = 1.0

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
