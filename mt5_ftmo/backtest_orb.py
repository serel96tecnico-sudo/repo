"""Sanity-check del ORB filtrado (vídeo Benchmark) sobre QQQ 5-min, volumen real
(Alpaca IEX). ¿Los filtros RVOL+VWAP convierten la esperanza negativa del ORB
crudo en positiva? Antes de construir el EA de MT5.

Reglas (fieles al vídeo):
  - Rango de apertura = high/low de 09:30–09:45 ET (tres velas de 5m).
  - Ruptura = primera vela que CIERRA fuera del rango (>high=long, <low=short),
    dentro de la ventana de entrada.
  - Entrada = apertura de la vela siguiente (a mercado).
  - SL = mitad del rango. TP = RR·riesgo (riesgo = |entrada−mitad|).
  - Filtros: RVOL (vol acumulado de sesión vs media de N días a esa hora) ≥ umbral;
    VWAP de sesión (long: precio>VWAP; short: precio<VWAP).
  - 1 trade/día (primera ruptura que pasa filtros). Sin fill → cierra a 16:00.

Compara 4 modos (crudo / solo RVOL / solo VWAP / ambos) × RR (1 y 2).
Cautela: IEX = volumen parcial (RVOL como ratio se sostiene; VWAP es proxy).
Uso: python backtest_orb.py
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

DAYS = 800
RVOL_N = 14                 # días para la media histórica de RVOL
RVOL_THR = 1.5
OR_END = "09:45"            # fin del rango de apertura
ENTRY_CUTOFF = "11:30"      # última hora para abrir (ET)
SESSION_END = "16:00"


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
    df["t"] = df.index.strftime("%H:%M")
    return df


def simulate(df, mode, rr, rvol_thr=RVOL_THR):
    """mode in {raw, rvol, vwap, both}. Devuelve lista de R por trade."""
    Rs = []
    # media histórica de volumen acumulado por slot horario (para RVOL)
    hist = {}  # slot -> list de cum_vol de días previos
    days = sorted(set(df["date"]))
    for d in days:
        day = df[df["date"] == d]
        day = day[(day["t"] >= "09:30") & (day["t"] < SESSION_END)]
        if len(day) < 10:
            continue
        o = day["open"].values; h = day["high"].values; l = day["low"].values
        c = day["close"].values; v = day["volume"].values; ts = day["t"].values
        # rango de apertura 09:30–09:45
        oi = [k for k in range(len(ts)) if "09:30" <= ts[k] < OR_END]
        if not oi:
            continue
        or_hi = h[oi].max(); or_lo = l[oi].min(); mid = (or_hi + or_lo) / 2
        if or_hi <= or_lo:
            continue
        # acumulados de sesión (VWAP y volumen) desde 09:30
        tp = (h + l + c) / 3
        cum_v = np.cumsum(v)
        cum_pv = np.cumsum(tp * v)
        vwap = np.where(cum_v > 0, cum_pv / cum_v, c)
        # RVOL en cada slot vs media histórica de ese slot
        rvol = np.ones(len(ts))
        for k in range(len(ts)):
            hv = hist.get(ts[k])
            if hv:
                m = np.mean(hv[-RVOL_N:])
                rvol[k] = cum_v[k] / m if m > 0 else 1.0

        # buscar primera ruptura válida tras el rango de apertura
        entered = False
        for k in range(len(ts)):
            if ts[k] < OR_END or ts[k] > ENTRY_CUTOFF:
                continue
            direction = 0
            if c[k] > or_hi:
                direction = 1
            elif c[k] < or_lo:
                direction = -1
            if direction == 0:
                continue
            # filtros
            ok_rvol = rvol[k] >= rvol_thr
            ok_vwap = (c[k] > vwap[k]) if direction == 1 else (c[k] < vwap[k])
            if mode == "rvol" and not ok_rvol:
                continue
            if mode == "vwap" and not ok_vwap:
                continue
            if mode == "both" and not (ok_rvol and ok_vwap):
                continue
            if k + 1 >= len(ts):
                break
            entry = o[k + 1]
            risk = abs(entry - mid)
            if risk <= 0:
                break
            if direction == 1:
                sl, tp_lvl = mid, entry + rr * risk
            else:
                sl, tp_lvl = mid, entry - rr * risk
            # simular forward intradía
            R = None
            for j in range(k + 1, len(ts)):
                if direction == 1:
                    if l[j] <= sl:
                        R = -1.0; break
                    if h[j] >= tp_lvl:
                        R = rr; break
                else:
                    if h[j] >= sl:
                        R = -1.0; break
                    if l[j] <= tp_lvl:
                        R = rr; break
            if R is None:
                R = (c[-1] - entry) / risk * direction  # cierre a fin de sesión
            Rs.append(R)
            entered = True
            break
        # actualizar histórico de RVOL con los acumulados de HOY (para días futuros)
        for k in range(len(ts)):
            hist.setdefault(ts[k], []).append(cum_v[k])
    return Rs


def report(Rs, label):
    if not Rs:
        print(f"  {label:16} sin trades"); return
    a = np.array(Rs)
    exp = a.mean()
    win = (a > 0).mean()
    print(f"  {label:16} n={len(a):>4}  win={win:>5.1%}  R/trade(exp)={exp:>+.3f}  "
          f"total={a.sum():>+7.1f}R")


def main():
    print(f"Descargando QQQ 5m ({DAYS}d, Alpaca IEX)...")
    df = fetch_5m("QQQ")
    print(f"Barras: {len(df)} | días: {len(set(df['date']))}\n")
    for rr in (1.0, 2.0):
        print(f"===== RR {rr:.0f}:1  (SL=mitad rango, ventana 09:45–{ENTRY_CUTOFF} ET) =====")
        for mode, lab in [("raw", "ORB crudo"), ("rvol", "+RVOL"),
                          ("vwap", "+VWAP"), ("both", "+RVOL+VWAP")]:
            report(simulate(df, mode, rr), lab)
        print()
    print("Lectura: la tesis del vídeo se valida si 'ORB crudo' es ~plano/negativo y "
          "'+RVOL+VWAP' sube claramente la esperanza. Cautela: IEX vol parcial, QQQ (no CFD).")


if __name__ == "__main__":
    main()
