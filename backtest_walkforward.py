"""Walk-forward: ¿los edges sobreviven fuera de muestra? Divide cada serie en
IS (1ª mitad temporal) y OOS (2ª mitad) y mide por separado. Si el edge de ≥8%
solo aparece en IS, es sobre-ajuste; si aguanta en OOS, es fiable.

Valida DOS cosas:
  (1) Entrada A (breakout) vs B (pullback EMA9) sobre señal momentum, por bucket
      → confirma la regla 'pullback en ≥8% ATR'
  (2) Señal momentum vs base-breakout ATR-relativo (coil<=5·ATR, K=20)
      → confirma la calidad del base-breakout, sobre todo en ≥8%

Entrada/salida base: A=EMA9+0.5·ATR (o B=EMA9 limit), 1.5/3.0, hold 15, fill 5.
Uso: python backtest_walkforward.py
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
COIL = 5
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
        if df is None or len(df) < WARMUP + 60:
            continue
        df = df.sort_index()
        c = df["Close"]
        series[t] = dict(
            o=df["Open"].values, h=df["High"].values, l=df["Low"].values, c=c.values,
            ema9=calculate_ema(c, 9).values, ema200=calculate_ema(c, 200).values,
            atr=calculate_atr(df["High"], df["Low"], c, 14).values)
    return series


def entry(mode, s, i):
    o, h, l, c, ema9, atr = s["o"], s["h"], s["l"], s["c"], s["ema9"], s["atr"]
    end = min(i + FILL_WIN, len(c) - 1)
    if mode == "B":
        lvl = ema9[i]
        for j in range(i + 1, end + 1):
            if l[j] <= lvl:
                return j, min(lvl, o[j])
        return None, None
    lvl = ema9[i] + STRENGTH_ATR * atr[i]
    for j in range(i + 1, end + 1):
        if h[j] >= lvl:
            return j, max(lvl, o[j])
    return None, None


def sim(s, i, mode):
    d0, fill = entry(mode, s, i)
    if d0 is None:
        return None
    l, h, c, atr = s["l"], s["h"], s["c"], s["atr"]
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


def is_momentum(s, i):
    return s["c"][i] >= s["c"][i - 9:i + 1].max()


def is_base(s, i):
    h, l, c, atr = s["h"], s["l"], s["c"], s["atr"]
    ch, cl = h[i - K:i].max(), l[i - K:i].min()
    tight = (ch - cl) <= COIL * atr[i] and atr[i] < atr[i - K]
    return tight and c[i] > ch and c[i - 1] <= ch


def collect(series, sig_fn, mode):
    """Devuelve {seg: {bucket: [R,...]}} con seg in {IS, OOS}."""
    res = {"IS": {lab: [] for lab in ATR_LABELS}, "OOS": {lab: [] for lab in ATR_LABELS}}
    for s in series.values():
        n = len(s["c"])
        split = WARMUP + (n - WARMUP) // 2
        last = -10**9
        for i in range(WARMUP, n - 1):
            atr = s["atr"][i]; ema200 = s["ema200"][i]
            if np.isnan(ema200) or np.isnan(atr) or atr <= 0:
                continue
            if not (s["c"][i] > ema200 and ema200 > s["ema200"][i - 20]):
                continue
            if not sig_fn(s, i):
                continue
            if i - last < COOLDOWN:
                continue
            last = i
            R = sim(s, i, mode)
            if R is None:
                continue
            seg = "IS" if i < split else "OOS"
            res[seg][ATR_LABELS[int(np.digitize(atr / s["c"][i], ATR_BINS[1:-1]))]].append(R)
    return res


def show(res, title):
    print(f"\n{title}")
    print(f"   {'bucket':6}{'IS n':>6}{'IS R':>8}{'IS win':>8}   {'OOS n':>7}{'OOS R':>8}{'OOS win':>8}")
    for lab in ATR_LABELS:
        a, b = res["IS"][lab], res["OOS"][lab]
        def fmt(x):
            return (f"{np.mean(x):+.3f}" if x else "   —")
        def w(x):
            return (f"{np.mean([v>0 for v in x]):.0%}" if x else "  —")
        print(f"   {lab:6}{len(a):>6}{fmt(a):>8}{w(a):>8}   {len(b):>7}{fmt(b):>8}{w(b):>8}")


def main():
    print("Cargando series...")
    series = load()
    print(f"Tickers: {len(series)}")
    print("\n" + "=" * 78)
    print("WALK-FORWARD (IS = 1ª mitad temporal · OOS = 2ª mitad)")
    print("=" * 78)

    print("\n(1) Regla de ENTRADA sobre señal MOMENTUM — ¿pullback gana en ≥8% OOS?")
    show(collect(series, is_momentum, "A"), "  A breakout-stop:")
    show(collect(series, is_momentum, "B"), "  B pullback-limit EMA9:")

    print("\n" + "-" * 78)
    print("(2) Calidad de SEÑAL — momentum vs base-breakout ATR-relativo (entrada A)")
    show(collect(series, is_momentum, "A"), "  S1 momentum:")
    show(collect(series, is_base, "A"), "  S2 base-breakout (coil<=5·ATR):")

    print("\nLectura: para fiarnos, el signo/rango del edge en ≥8% debe repetirse en OOS, "
          "no solo en IS. Muestra pequeña en ≥8% => mirar coherencia, no decimales.")


if __name__ == "__main__":
    main()
