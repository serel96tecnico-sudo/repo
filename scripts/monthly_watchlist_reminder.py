"""Recordatorio mensual: revisión de watchlist y fundamentales."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from utils.telegram_notifier import send_text
from datetime import datetime

msg = (
    f"REVISION MENSUAL — {datetime.now().strftime('%B %Y').upper()}\n"
    "\n"
    "Tareas pendientes:\n"
    "1. Revisar watchlist — tickers a añadir o eliminar\n"
    "2. Analisis fundamental manual de nuevas incorporaciones\n"
    "3. Limpiar fundamentals_cache.json (borrar tickers eliminados)\n"
    "4. Revisar rendimiento del pipeline del mes\n"
    "\n"
    "Ejecutar: python main.py --session morning"
)

if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
    send_text(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, msg)
    print("Recordatorio enviado por Telegram")
else:
    print(msg)
