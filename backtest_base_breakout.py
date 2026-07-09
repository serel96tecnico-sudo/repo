"""¿Comprar la ruptura de una BASE (canal/triángulo tras consolidación) bate a
perseguir el máximo de 10 días? Compara la CALIDAD DE SEÑAL, con entrada y salida
idénticas (aísla la señal). Complementa backtest_entry_modes/robustness.

Señales (todas exigen tendencia: close>EMA200 y EMA200 en pendiente +):
  S1  momentum      : close hace nuevo máximo de 10 sesiones            (actual)
  S2  base-breakout : consolidación tensa en K días (rango <= THRESH del
                      precio y ATR contraído) + hoy CIERRA por encima del
                      techo de la base y ayer aún estaba dentro (ruptura fresca)
  S2v base+volumen  : S2 y volumen de ruptura >= VOL_MULT · media(20)

Entrada (idéntica): breakout-stop A = EMA9 + 0.5·ATR, ventana 5.
Salida (idéntica):  stop 1.5·ATR / target 3.0·ATR, horizonte 15.  R = múltiplos.

Métricas por señal (global y por bucket de ATR%): nº, fill%, win%, %target,
%stop, R/trade. Uso: python backtest_base_breakout.py
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
WARMUP = 210
COOLDOWN = 5
STRENGTH_ATR = 0.5
FILL_WIN = 5
STOP_M, TGT_M, HOLD = 1.5, 3.0, 15
# base-breakout
K = 20                # ventana de consolidación
THRESH = 0.15         # rango de la base <= 15% del precio
VOL_MULT = 1.5        # confirmación de volumen
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
            o=df["Open"].values, h=df["High"].values, l=df["Low"].values,
            c=c.values, v=df["Volume"].values,
            ema9=calculate_ema(c, 9).values, ema200=calculate_ema(c, 200).values,
            atr=calculate_atr(df["High"], df["Low"], c, 14).values)
    return series


def gen_signals(s, kind):
    o, h, l, c, v = s["o"], s["h"], s["l"], s["c"], s["v"]
    ema200, atr = s["ema200"], s["atr"]
    n = len(c)
    sigs = []
    last = -10**9
    for i in range(WARMUP, n - 1):
        if np.isnan(ema200[i]) or np.isnan(atr[i]) or atr[i] <= 0:
            continue
        if not (c[i] > ema200[i] and ema200[i] > ema200[i - 20]):
            continue
        ok = False
        if kind == "S1":
            ok = c[i] >= c[i - 9:i + 1].max()
        else:
            cons_high = h[i - K:i].max()
            cons_low = l[i - K:i].min()
            width = (cons_high - cons_low) / c[i]
            tight = width <= THRESH and atr[i] < atr[i - K]
            breakout = c[i] > cons_high and c[i - 1] <= cons_high
            ok = tight and breakout
            if ok and kind == "S2v":
                ok = v[i] >= VOL_MULT * v[i - 20:i].mean()
        if not ok:
            continue
        if i - last < COOLDOWN:
            continue
        last = i
        sigs.append((i, atr[i] / c[i]))
    return sigs


def sim(s, i):
    o, h, l, c, ema9, atr = s["o"], s["h"], s["l"], s["c"], s["ema9"], s["atr"]
    level = ema9[i] + STRENGTH_ATR * atr[i]
    end_fill = min(i + FILL_WIN, len(c) - 1)
    d0 = fill = None
    for j in range(i + 1, end_fill + 1):
        if h[j] >= level:
            d0, fill = j, max(level, o[j])
            break
    if d0 is None:
        return None
    a = atr[i]
    stop, target = fill - STOP_M * a, fill + TGT_M * a
    end = min(d0 + HOLD, len(c) - 1)
    R, out = (c[end] - fill) / (STOP_M * a), "time"
    for j in range(d0 + 1, end + 1):
        if l[j] <= stop:
            R, out = -1.0, "stop"; break
        if h[j] >= target:
            R, out = TGT_M / STOP_M, "target"; break
    return R, out


def evaluate(series, kind):
    rows = []
    for s in series.values():
        for i, atrpct in gen_signals(s, kind):
            r = sim(s, i)
            lab = ATR_LABELS[int(np.digitize(atrpct, ATR_BINS[1:-1]))]
            if r is None:
                rows.append((lab, None, None))
            else:
                rows.append((lab, r[0], r[1]))
    return rows


def summarize(rows, name):
    n = len(rows)
    filled = [(l_, R, o_) for (l_, R, o_) in rows if R is not None]
    fr = len(filled) / n if n else 0
    Rs = [R for _, R, _ in filled]
    win = np.mean([R > 0 for R in Rs]) if Rs else float("nan")
    tgt = np.mean([o_ == "target" for _, _, o_ in filled]) if filled else float("nan")
    stp = np.mean([o_ == "stop" for _, _, o_ in filled]) if filled else float("nan")
    rmean = np.mean(Rs) if Rs else float("nan")
    print(f"{name:14} n={n:>5} fill={fr:>4.0%} win={win:>4.0%} "
          f"tgt={tgt:>4.0%} stop={stp:>4.0%}  R/trade={rmean:>+.3f}")
    return rows


def by_bucket(rows, name):
    print(f"\n{name} — por bucket ATR%:")
    for lab in ATR_LABELS:
        sub = [(R, o_) for (l_, R, o_) in rows if l_ == lab]
        filled = [(R, o_) for (R, o_) in sub if R is not None]
        if not sub:
            continue
        Rs = [R for R, _ in filled]
        rmean = np.mean(Rs) if Rs else float("nan")
        win = np.mean([R > 0 for R in Rs]) if Rs else float("nan")
        print(f"   {lab:5} n={len(sub):>4} fill={len(filled)/len(sub):>4.0%} "
              f"win={win:>4.0%} R/trade={rmean:>+.3f}")


def main():
    print("Cargando series...")
    series = load()
    print(f"Tickers: {len(series)}\n")
    print("=" * 74)
    print("CALIDAD DE SEÑAL (entrada/salida idénticas: A breakout, 1.5/3.0, h15)")
    print("=" * 74)
    results = {}
    for kind, name in [("S1", "S1 momentum"), ("S2", "S2 base-break"), ("S2v", "S2 base+vol")]:
        results[kind] = summarize(evaluate(series, kind), name)
    for kind, name in [("S1", "S1 momentum"), ("S2", "S2 base-break"), ("S2v", "S2 base+vol")]:
        by_bucket(results[kind], name)
    print("\nNota: menos señales pero mejor R/trade = señal de más calidad. "
          "Comparar sobre todo el bucket >=8% (el caso CIFR).")


if __name__ == "__main__":
    main()
