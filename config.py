from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(override=True)

ROOT_DIR = Path(__file__).parent
CONTEXT_DIR = ROOT_DIR / "contex"
OUTPUT_DIR = ROOT_DIR / "output"
LOGS_DIR = ROOT_DIR / "output" / "logs"

ANTHROPIC_MODEL = "claude-sonnet-4-6"
ANTHROPIC_MAX_TOKENS = 4096
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

SCAN_MAX_CANDIDATES = 50
FUNDAMENTAL_TOP_N = 35
TA_TOP_N = 15
SENTIMENT_TOP_N = 10
RISK_TOP_N = 10
FINAL_REPORT_N = 4
EARNINGS_BLOCK_DAYS = 3

MIN_PRICE = 4.0
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
