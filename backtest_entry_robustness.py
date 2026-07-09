"""Robustez del hallazgo 'pullback-limit gana en ATR alto' (complementa
backtest_entry_modes.py). Descarga una vez, fija las señales (no dependen de los
parámetros de entrada) y barre:
  - nivel de pullback:  EMA9 - k·ATR,  k ∈ {0, 0.25, 0.5}
  - bracket salida:     (stop·ATR, target·ATR) ∈ {(1,2), (1.5,3), (2,4)}
  - horizonte:          HOLD ∈ {10, 15, 20}
  - ventana de fill:    {3, 5}

Dos métricas por celda (para no engañarnos con la tasa de fill de B):
  - R/trade   = expectativa media sobre órdenes que rellenan
  - R/señal   = fill_rate · R/trade  (capital no desplegado si no rellena = 0)

Objetivo: confirmar que el signo de (B - A) en ATR alto es estable, y elegir el
umbral de ATR% para conmutar de breakout (A) a pullback (B). Uso:
  python backtest_entry_robustness.py
"""

import json
import sys

import numpy as np
import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import CONTEXT_DIR
from data.market_data import MarketDataFetcher
from data.indicators import calculate_ema, calculate_atr

HISTORY = "1000d"
COOLDOWN = 5
WARMUP = 210
STRENGTH_ATR = 0.5          # buffer del breakout (A), fijo (def. R5)
ATR_BINS = [0, 0.03, 0.05, 0.08, 9.99]
ATR_LABELS = ["<3%", "3-5%", "5-8%", ">=8%"]


def load_series():
    tickers = sorted(set(json.load(open("contex/watchlist.json", encoding="utf-8")).get("tickers", [])))
    fetcher = MarketDataFetcher(CONTEXT_DIR)
    series, signals = {}, []
    for t in tickers:
        try:
            df = fetcher._fetch_ohlcv_alpaca(t, period=HISTORY, timeframe="day")
        except Exception:
            df = None
        if df is None or len(df) < WARMUP + 30:
            continue
        df = df.sort_index()
        c = df["Close"]
        ema9 = calculate_ema(c, 9).values
        ema200 = calculate_ema(c, 200).values
        atr = calculate_atr(df["High"], df["Low"], c, 14).values
        o, h, l, cc = df["Open"].values, df["High"].values, df["Low"].values, c.values
        series[t] = (o, h, l, cc, ema9, atr)
        n = len(cc)
        last = -10**9
        for i in range(WARMUP, n - 1):
            if np.isnan(ema200[i]) or np.isnan(atr[i]) or atr[i] <= 0:
                continue
            if not (cc[i] > ema200[i] and ema200[i] > ema200[i - 20]):
                continue
            if cc[i] < cc[i - 9:i + 1].max():
                continue
            if i - last < COOLDOWN:
                continue
            last = i
            signals.append((t, i, atr[i] / cc[i]))
    return series, signals


def entry(mode, arr, i, k_pull, fill_win):
    o, h, l, cc, ema9, atr = arr
    end = min(i + fill_win, len(cc) - 1)
    if mode == "B":
        level = ema9[i] - k_pull * atr[i]
        for j in range(i + 1, end + 1):
            if l[j] <= level:
                return j, min(level, o[j])
        return None, None
    level = ema9[i] + STRENGTH_ATR * atr[i]
    for j in range(i + 1, end + 1):
        if h[j] >= level:
            return j, max(level, o[j])
    return None, None


def sim(arr, i, mode, k_pull, fill_win, stop_m, tgt_m, hold):
    o, h, l, cc, ema9, atr = arr
    d0, fill = entry(mode, arr, i, k_pull, fill_win)
    if d0 is None:
        return None
    a = atr[i]
    stop, target = fill - stop_m * a, fill + tgt_m * a
    end = min(d0 + hold, len(cc) - 1)
    R = (cc[end] - fill) / (stop_m * a)
    for j in range(d0 + 1, end + 1):
        if l[j] <= stop:
            R = -1.0
            break
        if h[j] >= target:
            R = tgt_m / stop_m
            break
    return R


def run(series, signals, k_pull, fill_win, stop_m, tgt_m, hold):
    """Devuelve dict bucket -> {mode -> (n, fill_rate, R_trade, R_signal)}."""
    acc = {lab: {"A": [], "B": []} for lab in ATR_LABELS}
    for t, i, atrpct in signals:
        lab = ATR_LABELS[int(np.digitize(atrpct, ATR_BINS[1:-1]))]
        arr = series[t]
        for mode in ("A", "B"):
            acc[lab][mode].append(sim(arr, i, mode, k_pull, fill_win, stop_m, tgt_m, hold))
    out = {}
    for lab in ATR_LABELS:
        out[lab] = {}
        for mode in ("A", "B"):
            vals = acc[lab][mode]
            filled = [v for v in vals if v is not None]
            n = len(vals)
            fr = len(filled) / n if n else 0
            rt = float(np.mean(filled)) if filled else float("nan")
            rs = fr * rt if filled else float("nan")
            out[lab][mode] = (n, fr, rt, rs)
    return out


def main():
    print("Cargando series y señales (una sola vez)...")
    series, signals = load_series()
    print(f"Tickers: {len(series)} | señales: {len(signals)}\n")

    base = dict(k_pull=0.0, fill_win=5, stop_m=1.5, tgt_m=3.0, hold=15)

    # ── Sweep 1: nivel de pullback (k) × ventana de fill ──────────────────────
    print("=" * 82)
    print("SWEEP 1 — nivel de pullback B (EMA9 - k·ATR).  R/trade | R/señal por bucket")
    print("bracket 1.5/3.0, hold 15.  A(breakout) de referencia arriba.")
    print("=" * 82)
    refA = run(series, signals, **base)
    print(f"{'':22}" + "".join(f"{lab:>16}" for lab in ATR_LABELS))
    print(f"{'A breakout (ref)':22}" + "".join(
        f"{refA[lab]['A'][2]:>7.3f}|{refA[lab]['A'][3]:>7.3f}" for lab in ATR_LABELS))
    for fw in (3, 5):
        for k in (0.0, 0.25, 0.5):
            r = run(series, signals, k_pull=k, fill_win=fw, stop_m=1.5, tgt_m=3.0, hold=15)
            tag = f"B k={k} fill={fw}"
            print(f"{tag:22}" + "".join(
                f"{r[lab]['B'][2]:>7.3f}|{r[lab]['B'][3]:>7.3f}" for lab in ATR_LABELS))

    # ── Sweep 2: robustez de (B - A) en ATR alto por bracket × hold ───────────
    print("\n" + "=" * 82)
    print("SWEEP 2 — estabilidad de (B−A) por bracket × hold.  Valor = R/trade (B − A)")
    print("k pullback=0 (EMA9), fill=5.  '+' => B mejor.  Buckets 5-8% y >=8%.")
    print("=" * 82)
    print(f"{'bracket / hold':18}{'5-8% B-A':>12}{'>=8% B-A':>12}   (R/señal B-A)")
    for (sm, tm) in [(1.0, 2.0), (1.5, 3.0), (2.0, 4.0)]:
        for hold in (10, 15, 20):
            r = run(series, signals, k_pull=0.0, fill_win=5, stop_m=sm, tgt_m=tm, hold=hold)
            d58 = r["5-8%"]["B"][2] - r["5-8%"]["A"][2]
            d8 = r[">=8%"]["B"][2] - r[">=8%"]["A"][2]
            s58 = r["5-8%"]["B"][3] - r["5-8%"]["A"][3]
            s8 = r[">=8%"]["B"][3] - r[">=8%"]["A"][3]
            print(f"{f'{sm}/{tm}  h={hold}':18}{d58:>+12.3f}{d8:>+12.3f}   "
                  f"({s58:+.3f} / {s8:+.3f})")

    # ── Sweep 3: umbral de conmutación (hybrid A/B) vs A puro ─────────────────
    print("\n" + "=" * 82)
    print("SWEEP 3 — regla híbrida (B si ATR%>=umbral, si no A) vs A puro.")
    print("Métrica: R/señal global ponderado por nº de señales de cada bucket.")
    print("=" * 82)
    def weighted_signal_R(r, rule):
        num = den = 0
        for lab in ATR_LABELS:
            nA, frA, rtA, rsA = r[lab]["A"]
            _, _, _, rsB = r[lab]["B"]
            use_B = rule(lab)
            rs = rsB if use_B else rsA
            if rs == rs:
                num += rs * nA
                den += nA
        return num / den if den else float("nan")
    for (sm, tm) in [(1.5, 3.0), (2.0, 4.0)]:
        r = run(series, signals, k_pull=0.0, fill_win=5, stop_m=sm, tgt_m=tm, hold=15)
        pureA = weighted_signal_R(r, lambda lab: False)
        pureB = weighted_signal_R(r, lambda lab: True)
        h8 = weighted_signal_R(r, lambda lab: lab == ">=8%")
        h5 = weighted_signal_R(r, lambda lab: lab in (">=8%", "5-8%"))
        print(f"bracket {sm}/{tm}:  A_puro {pureA:.4f} | híbrido≥8% {h8:.4f} | "
              f"híbrido≥5% {h5:.4f} | B_puro {pureB:.4f}")

    print("\nNota: R/trade=calidad por operación; R/señal=calidad·fill (cuenta que B "
          "rellena menos). Umbral robusto = donde B gana en ambas métricas y en todos los brackets.")


if __name__ == "__main__":
    main()
