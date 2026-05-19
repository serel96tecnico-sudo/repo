"""Sondeo de sentimiento macro + cartera para apertura del lunes."""
import warnings
warnings.filterwarnings("ignore")

import httpx
import anthropic
from config import ANTHROPIC_API_KEY
from data.news_fetcher import NewsFetcher

TICKERS_MACRO = ["SPY", "QQQ", "^VIX", "BTC-USD", "ETH-USD"]
TICKERS_CARTERA = ["IONQ", "NVO", "DLO", "PYPL", "ASTS", "BMW"]

SYSTEM = """Eres un analista de mercados financieros especializado en apertura de sesión americana.
Se te darán titulares de noticias del fin de semana y el viernes.
Tu tarea: evaluar cómo abrirá el mercado el próximo lunes y qué impacto tendrá en las posiciones indicadas.
Responde SIEMPRE en español. Sé directo y concreto."""

client = anthropic.Anthropic(
    api_key=ANTHROPIC_API_KEY,
    http_client=httpx.Client(verify=False),
)

fetcher = NewsFetcher(delay_seconds=1.0)
# SSL bypass for requests session
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

user_msg = f"""Noticias recientes (fin de semana / viernes):
{news_block}

Posiciones en cartera:
- IONQ (largo, BEP $46.43, SL $44.00) — Quantum Computing
- NVO (largo, BEP $44.00, SL $42.50, TP $50.00) — Healthcare
- BMW.DE (largo, BEP €83.24, SL €79.00, TP €91.00) — Automotive
- DLO (largo, BEP $13.82, SL $12.50, TP $14.55) — Resultados Q1 el martes 13/05
- PYPL (largo, BEP $49.32, SL $44.00, TP $56.00) — Fintech. SOLO 3.4% sobre el SL
- ASTS (largo, BEP $73.84, SL $64.00, TP $83.00) — Satélites

Proporciona:
1. APERTURA ESPERADA: ¿alcista, bajista o neutral? ¿por qué?
2. RIESGOS principales para el lunes
3. POSICIONES EN PELIGRO: ¿alguna cerca del SL por las noticias?
4. OPORTUNIDADES: ¿algo positivo para la cartera?
5. RECOMENDACIÓN: qué vigilar en la apertura (primeros 30 min)"""

print("\nAnalizando con Claude...\n")
print("=" * 60)
print("SONDEO APERTURA LUNES — 12/05/2026")
print("=" * 60)

resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=1500,
    system=SYSTEM,
    messages=[{"role": "user", "content": user_msg}],
)
output = resp.content[0].text
print(output.encode("cp1252", errors="replace").decode("cp1252"))
print("\n" + "=" * 60)
