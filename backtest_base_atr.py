"""¿Existe un base-breakout ÚTIL en nombres calientes si el 'coil' se define
relativo al ATR del propio nombre (en vez de % del precio)? Cierra la pregunta
abierta de backtest_base_breakout.py.

Coil ATR-relativo: rango de la base en K días <= COIL·ATR  (contracción respecto
a SU normalidad; 20 sesiones de random walk ~ 4-5 ATR, así que <=4-6 ATR = tenso)
+ ATR contraído + hoy cierra sobre el techo de la base (ruptura fresca).

Entrada/salida idénticas (A breakout 0.5·ATR, 1.5/3.0, h15). Sweep COIL ∈ {4,5,6}.
Reporta por bucket ATR%, con foco en >=8%. Uso: python backtest_base_atr.py
"""

import json
import sys

import numpy as np

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import CONTEXT_DIR
from data.market_data import MarketDataFetcher
from data.indicators import calculate_ema, calculate_atr

HISTORY = "1000d"
WARMUP, COOLDOWN, K = 210, 5, 20
STRENGTH_ATR, FILL_WIN = 0.5, 5
STOP_M, TGT_M, HOLD = 1.5, 3.0, 15
ATR_BINS = [0, 0.03, 0.05, 0.08, 9.99]
ATR_LABELS = ["<3%", "3-5%", "5-8%", ">=8%"]


def load():
    tickers = sorted(set(json.load(open("contex/watchlist.json", encoding="utf-8")).get("tickers", [])))
    f = MarketDataFetcher(CONTEXT_DIR)
    series = {}
    for t in tickers:
        try:
            df = f._fetch_ohlcv_alpaca(t, period=HISTORY, timeframe="day")
        except Exception:
            df = None
        if df is None or len(df) < WARMUP + 30:
            continue
        df = df.sort_index()
        c = df["Close"]
        series[t] = dict(
            o=df["Open"].values, h=df["High"].values, l=df["Low"].values, c=c.values,
            ema9=calculate_ema(c, 9).values, ema200=calculate_ema(c, 200).values,
            atr=calculate_atr(df["High"], df["Low"], c, 14).values)
    return series


def sim(s, i):
    o, h, l, c, ema9, atr = s["o"], s["h"], s["l"], s["c"], s["ema9"], s["atr"]
    level = ema9[i] + STRENGTH_ATR * atr[i]
    d0 = fill = None
    for j in range(i + 1, min(i + FILL_WIN, len(c) - 1) + 1):
        if h[j] >= level:
            d0, fill = j, max(level, o[j]); break
    if d0 is None:
        return None
    a = atr[i]
    stop, target = fill - STOP_M * a, fill + TGT_M * a
    end = min(d0 + HOLD, len(c) - 1)
    R = (c[end] - fill) / (STOP_M * a)
    for j in range(d0 + 1, end + 1):
        if l[j] <= stop:
            return -1.0
        if h[j] >= target:
            return TGT_M / STOP_M
    return R


def signals_atr_coil(s, coil):
    o, h, l, c, ema200, atr = s["o"], s["h"], s["l"], s["c"], s["ema200"], s["atr"]
    n = len(c); sigs = []; last = -10**9
    for i in range(WARMUP, n - 1):
        if np.isnan(ema200[i]) or np.isnan(atr[i]) or atr[i] <= 0:
            continue
        if not (c[i] > ema200[i] and ema200[i] > ema200[i - 20]):
            continue
        cons_high = h[i - K:i].max(); cons_low = l[i - K:i].min()
        tight = (cons_high - cons_low) <= coil * atr[i] and atr[i] < atr[i - K]
        breakout = c[i] > cons_high and c[i - 1] <= cons_high
        if not (tight and breakout):
            continue
        if i - last < COOLDOWN:
            continue
        last = i
        sigs.append((i, atr[i] / c[i]))
    return sigs


def main():
    print("Cargando series...")
    series = load()
    print(f"Tickers: {len(series)}\n")
    print("=" * 72)
    print("BASE-BREAKOUT con COIL RELATIVO AL ATR (rango base <= COIL·ATR)")
    print("entrada/salida idénticas.  R/trade por bucket ATR%.")
    print("=" * 72)
    for coil in (4, 5, 6):
        rows = []
        for s in series.values():
            for i, atrpct in signals_atr_coil(s, coil):
                R = sim(s, i)
                rows.append((ATR_LABELS[int(np.digitize(atrpct, ATR_BINS[1:-1]))], R))
        tot = len(rows)
        Rall = [R for _, R in rows if R is not None]
        print(f"\nCOIL={coil}·ATR   total n={tot}  R/trade global={np.mean(Rall):+.3f}" if Rall else f"\nCOIL={coil}: sin señales")
        for lab in ATR_LABELS:
            sub = [R for l_, R in rows if l_ == lab]
            fill = [R for R in sub if R is not None]
            if not sub:
                continue
            r = np.mean(fill) if fill else float("nan")
            win = np.mean([R > 0 for R in fill]) if fill else float("nan")
            print(f"   {lab:5} n={len(sub):>4}  win={win:>4.0%}  R/trade={r:>+.3f}")


if __name__ == "__main__":
    main()
