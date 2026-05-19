"""Sondeo de sentimiento de mercado para la sesion del dia."""
import warnings
warnings.filterwarnings("ignore")

import httpx
import anthropic
from config import ANTHROPIC_API_KEY
from data.news_fetcher import NewsFetcher

TICKERS_MACRO = ["SPY", "QQQ", "^VIX", "BTC-USD", "ETH-USD"]
TICKERS_CARTERA = ["IONQ", "NVO", "DLO", "PYPL", "SMR", "BMW", "RKLB"]

SYSTEM = """Eres un analista de mercados financieros especializado en sesion americana.
Se te darán titulares de noticias del dia y recientes.
Tu tarea: evaluar el sentimiento actual del mercado y el impacto en las posiciones indicadas.
Responde SIEMPRE en español. Sé directo y concreto."""

client = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    http_client=httpx.Client(verify=False),
)

fetcher = NewsFetcher(delay_seconds=1.0)
fetcher._session.verify = False

all_news = {}
print("Obteniendo noticias...")
for tk in TICKERS_MACRO + TICKERS_CARTERA:
    items = fetcher.fetch_all(tk.replace("^", ""))
    if items:
        all_news[tk] = items[:8]
        print(f"  {tk}: {len(items[:8])} noticias")
    else:
        print(f"  {tk}: sin noticias")

news_block = ""
for tk, items in all_news.items():
    news_block += f"\n### {tk}\n"
    for it in items:
        news_block += f"- [{it.source}] {it.title} ({it.published})\n"

user_msg = f"""Noticias del dia (12/05/2026):
{news_block}

Posiciones actuales en cartera:

BROKER 1 (largo, EUR):
- IONQ @ $56.84 (BEP $46.43, SL $51.50, sin TP) — Quantum Computing. +21% ayer.
- NVO @ $46.40 (BEP $44.00, SL $45.00, TP $50/$55) — Healthcare/Ozempic
- BMW.DE @ €80.90 (BEP €83.24, SL €79.00, TP €91.00) — Automotive
- RIB @ €13.12 (BEP €13.12, SL €12.50, TP €15.00) — Tech/Semiconductores
- NGAS @ $5.28 (BEP $5.31, TP $5.96) — Gas natural ETC

BROKER 2 (largo/corto, USD):
- SMR @ $13.20 (BEP $13.20, SL $10.65, TP $18.00) — Nuclear/Energia. Abierto ayer.
- DLO @ $13.82 (BEP $13.82, SL $12.50, TP $15.10) — Fintech. EARNINGS Q1 HOY MARTES 12/05.
- PYPL @ $49.32 (BEP $49.32, SL $43.50, TP $56.00) — Fintech. Resultados Q1 publicados: batió EPS pero guidance flojo. Cae ~8% en premarket a $45.23.

Proporciona:
1. SENTIMIENTO GENERAL: ¿como esta el mercado hoy? indices, macro
2. POSICIONES CRITICAS: PYPL con caida premarket, DLO con earnings hoy
3. CATALIZADORES: noticias positivas/negativas para cada posicion
4. RECOMENDACION: que hacer con PYPL antes de la apertura? aguantar DLO hasta earnings?
5. OPORTUNIDADES: algo interesante en el mercado hoy"""

print("\nAnalizando con Claude...\n")
print("=" * 60)
print("SENTIMIENTO DE MERCADO — MARTES 12/05/2026")
print("=" * 60)

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1800,
    system=SYSTEM,
    messages=[{"role": "user", "content": user_msg}],
)
output = resp.content[0].text
print(output.encode("cp1252", errors="replace").decode("cp1252"))
print("\n" + "=" * 60)
