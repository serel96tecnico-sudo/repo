from data.market_data import MarketDataFetcher
from config import CONTEXT_DIR
from data.indicators import calculate_all_indicators

f = MarketDataFetcher(CONTEXT_DIR)
df = f.fetch_ohlcv("PYPL", "90d")
i = calculate_all_indicators(df)

price = i["price"]
prev  = i["price_prev"]
chg   = (price - prev) / prev * 100
bep, sl = 49.32, 44.00

rsi     = i["rsi_14"]
macd_h  = i["macd_histogram"]
ema9    = i["ema9"]
ema21   = i["ema21"]
ema50   = i["ema50"]
adx     = i["adx_14"]
atr     = i["atr_14"]
bbu     = i["bb_upper"]
bbl     = i["bb_lower"]
vol_r   = i["volume_ratio_20d"]
sups    = [round(s, 2) for s in i["support_levels"][:4]]
ress    = [round(r, 2) for r in i["resistance_levels"][:4]]
h52     = i["high_52w"]
l52     = i["low_52w"]

stack = ("precio " + ("SOBRE" if price > ema9 else "BAJO") +
         " EMA9 | EMA9 " + (">" if ema9 > ema21 else "<") +
         " EMA21 | EMA21 " + (">" if ema21 > ema50 else "<") + " EMA50")

print("")
print("=" * 52)
print(f"ANALISIS TECNICO -- PYPL  |  ${price:.2f} ({chg:+.2f}%)")
print("=" * 52)
print(f"RSI(14):      {rsi:.1f}  {'<< SOBREVENDIDO' if rsi < 35 else '>> SOBRECOMPRADO' if rsi > 70 else ''}")
print(f"MACD hist:    {macd_h:.4f}  ({'alcista' if macd_h > 0 else 'bajista'})")
print(f"EMA 9/21/50:  {ema9:.2f} / {ema21:.2f} / {ema50:.2f}")
print(f"ADX(14):      {adx:.1f}  ({'tendencia fuerte' if adx > 25 else 'tendencia debil'})")
print(f"ATR(14):      ${atr:.2f}")
print(f"Bollinger:    sup ${bbu:.2f} | inf ${bbl:.2f}")
print(f"52w:          max ${h52:.2f} | min ${l52:.2f}")
print(f"Volumen:      {vol_r:.2f}x promedio 20d")
print(f"Soportes:     {sups}")
print(f"Resistencias: {ress}")
print("")
print(f"EMA stack:    {stack}")
print(f"Dist SL $44:  {((price - sl) / price * 100):+.1f}%")
print(f"Dist BEP:     {((price - bep) / price * 100):+.1f}%")
print("=" * 52)
