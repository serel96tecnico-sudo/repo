"""Barrido + walk-forward del ORB filtrado (QQQ 5m). Busca una config con edge
que aguante fuera de muestra y sea lo bastante grande para sobrevivir costes de
CFD (objetivo orientativo: OOS ≥ +0.25R con nº de trades decente).

Solo RVOL como filtro (el VWAP resultó redundante en backtest_orb.py). Barre:
  - rango de apertura: 15 / 30 / 60 min
  - umbral RVOL: off / 1.5 / 2.0 / 2.5
  - RR: 2 / 3
  - filtro de tendencia: off / EMA200 diaria (solo longs en alcista, shorts en bajista)
  - ventana de entrada fija: rango→+2h

Cada config se evalúa en IS (1ª mitad) y OOS (2ª mitad). Uso: python backtest_orb_sweep.py
"""

import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.market_data import MarketDataFetcher
from data.indicators import calculate_ema

DAYS = 800
RVOL_N = 14
ENTRY_WINDOW_MIN = 120       # ventana de entrada tras el rango (min)


def _m(s):
    h, mm = s.split(":"); return int(h) * 60 + int(mm)


def fetch_5m(symbol):
    f = MarketDataFetcher("contex")
    client = f._get_alpaca_client()
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                           start=datetime.now() - timedelta(days=DAYS), end=datetime.now(), feed="iex")
    df = client.get_stock_bars(req).df
    if isinstance(df.index, pd.MultiIndex):
        df = df.xs(symbol, level="symbol")
    df.index = pd.to_datetime(df.index).tz_convert("America/New_York")
    df = df[["open", "high", "low", "close", "volume"]].copy()
    df["date"] = df.index.date
    df["min"] = df.index.hour * 60 + df.index.minute
    return df


def daily_trend_map():
    """date -> +1 (close>EMA200) / -1 / 0 (sin dato)."""
    f = MarketDataFetcher("contex")
    d = f._fetch_ohlcv_alpaca("QQQ", period="1200d", timeframe="day").sort_index()
    ema = calculate_ema(d["Close"], 200)
    out = {}
    for i in range(len(d)):
        e = ema.iloc[i]
        out[d.index[i].date()] = 0 if pd.isna(e) else (1 if d["Close"].iloc[i] > e else -1)
    return out


def simulate(df, or_min, rvol_thr, rr, trend, trend_map):
    """Devuelve lista (date, R). rvol_thr=None => sin filtro; trend in {None,'daily'}."""
    or_end = 570 + or_min                      # 09:30 = 570 min
    entry_end = or_end + ENTRY_WINDOW_MIN
    out = []
    hist = {}
    for d in sorted(set(df["date"])):
        day = df[df["date"] == d]
        day = day[(day["min"] >= 570) & (day["min"] < 960)]      # 09:30–16:00
        if len(day) < 10:
            continue
        o = day["open"].values; h = day["high"].values; l = day["low"].values
        c = day["close"].values; v = day["volume"].values; mn = day["min"].values
        oi = np.where((mn >= 570) & (mn < or_end))[0]
        if len(oi) == 0:
            continue
        or_hi = h[oi].max(); or_lo = l[oi].min(); mid = (or_hi + or_lo) / 2
        cum_v = np.cumsum(v)
        rvol = np.ones(len(mn))
        if rvol_thr is not None:
            for k in range(len(mn)):
                hv = hist.get(mn[k])
                if hv:
                    m = np.mean(hv[-RVOL_N:])
                    rvol[k] = cum_v[k] / m if m > 0 else 1.0
        tdir = trend_map.get(d, 0) if trend == "daily" else None
        for k in range(len(mn)):
            if mn[k] < or_end or mn[k] > entry_end:
                continue
            direction = 1 if c[k] > or_hi else (-1 if c[k] < or_lo else 0)
            if direction == 0:
                continue
            if rvol_thr is not None and rvol[k] < rvol_thr:
                continue
            if trend == "daily" and tdir != 0 and direction != tdir:
                continue
            if k + 1 >= len(mn):
                break
            entry = o[k + 1]; risk = abs(entry - mid)
            if risk <= 0:
                break
            sl = mid; tp = entry + rr * risk * direction
            R = None
            for j in range(k + 1, len(mn)):
                if direction == 1:
                    if l[j] <= sl: R = -1.0; break
                    if h[j] >= tp: R = rr; break
                else:
                    if h[j] >= sl: R = -1.0; break
                    if l[j] <= tp: R = rr; break
            if R is None:
                R = (c[-1] - entry) / risk * direction
            out.append((d, R))
            break
        for k in range(len(mn)):
            hist.setdefault(mn[k], []).append(cum_v[k])
    return out


def stats(rows):
    if not rows:
        return (0, float("nan"), float("nan"))
    a = np.array([r for _, r in rows])
    return (len(a), (a > 0).mean(), a.mean())


def main():
    print(f"Descargando QQQ 5m ({DAYS}d) + tendencia diaria...")
    df = fetch_5m("QQQ")
    tmap = daily_trend_map()
    days = sorted(set(df["date"]))
    split = days[len(days) // 2]
    print(f"Barras: {len(df)} | días: {len(days)} | split OOS desde {split}\n")

    grid = []
    for or_min in (15, 30, 60):
        for rvol_thr in (None, 1.5, 2.0, 2.5):
            for rr in (2.0, 3.0):
                for trend in (None, "daily"):
                    grid.append((or_min, rvol_thr, rr, trend))

    results = []
    for (or_min, rvol_thr, rr, trend) in grid:
        rows = simulate(df, or_min, rvol_thr, rr, trend, tmap)
        is_rows = [r for r in rows if r[0] < split]
        oos_rows = [r for r in rows if r[0] >= split]
        ni, wi, ei = stats(is_rows)
        no, wo, eo = stats(oos_rows)
        results.append((or_min, rvol_thr, rr, trend, ni, ei, no, wo, eo))

    results.sort(key=lambda x: (x[8] if x[8] == x[8] else -9), reverse=True)
    print(f"{'OR':>3} {'RVOL':>5} {'RR':>3} {'trend':>6} | {'IS n':>5}{'IS exp':>8} | {'OOS n':>6}{'OOS win':>8}{'OOS exp':>8}  robusto")
    print("-" * 78)
    for (or_min, rvol_thr, rr, trend, ni, ei, no, wo, eo) in results:
        rob = "✓" if (ei == ei and eo == eo and ei > 0 and eo > 0.15 and no >= 25) else ""
        rv = "off" if rvol_thr is None else f"{rvol_thr}"
        tr = trend or "off"
        print(f"{or_min:>3} {rv:>5} {rr:>3.0f} {tr:>6} | {ni:>5}{ei:>+8.3f} | "
              f"{no:>6}{wo:>8.1%}{eo:>+8.3f}  {rob}")
    print("\n✓ = IS>0 y OOS>+0.15R y OOS n>=25. Cuidado con configs de OOS n bajo (poca fiabilidad).")


if __name__ == "__main__":
    main()
