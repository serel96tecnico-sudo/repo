"""¿Diversifica el DAX al oro? Combina las dos estrategias (mismo Donchian
100/1.5/3.0) en una sola cuenta y mide si el DRAWDOWN COMBINADO baja respecto a
cada una por separado — que es lo que permitiría subir el riesgo con seguridad.

Proxy (GC=F, ^GDAXI H1, yfinance). Direccional. Uso: python backtest_portfolio_combo.py
"""
import sys
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pandas as pd
from backtest_mt4_gold_silver_h1 import fetch_h1, signal_donchian
from data.indicators import calculate_atr

RISK = 0.004          # 0.4% por trade
N, SM, TM = 100, 1.5, 3.0


def trade_returns(ticker, cost_bps):
    """Devuelve serie de retornos por trade (fracción de cuenta) indexada por fecha de salida."""
    df = fetch_h1(ticker)
    atr = calculate_atr(df["High"], df["Low"], df["Close"], 14).to_numpy()
    ls, ss = signal_donchian(df, N, False)
    o = df["Open"].to_numpy(); h = df["High"].to_numpy(); l = df["Low"].to_numpy(); c = df["Close"].to_numpy()
    la = ls.to_numpy(); sa = ss.to_numpy()
    idx = df.index
    cost = cost_bps / 10000.0
    out = []
    i = 1; in_pos = False; d = 0; entry = stop = tgt = 0.0
    while i < len(df):
        if in_pos:
            hit_s = (l[i] <= stop) if d == 1 else (h[i] >= stop)
            hit_t = (h[i] >= tgt) if d == 1 else (l[i] <= tgt)
            ex = stop if hit_s else (tgt if hit_t else (c[i] if i == len(df)-1 else None))
            if ex is not None:
                raw = (ex - entry) if d == 1 else (entry - ex)
                pnl = raw - cost*entry
                r = pnl / abs(entry - stop) if entry != stop else 0.0
                out.append((idx[i], r * RISK))     # retorno en fracción de cuenta
                in_pos = False
        if not in_pos:
            a = atr[i-1]
            if not np.isnan(a) and a > 0:
                if la[i-1]:
                    d=1; entry=o[i]; stop=entry-SM*a; tgt=entry+TM*a; in_pos=True
                elif sa[i-1]:
                    d=-1; entry=o[i]; stop=entry+SM*a; tgt=entry-TM*a; in_pos=True
        i += 1
    s = pd.Series([r for _,r in out], index=[t for t,_ in out])
    return s.groupby(s.index.normalize()).sum()   # retorno diario


def stats(daily, label):
    eq = (1 + daily).cumprod()
    peak = eq.cummax()
    dd = (eq/peak - 1)
    ret = (eq.iloc[-1]-1)*100
    print(f"  {label:22} retorno {ret:>+7.1f}%   DD máx {dd.min()*100:>6.2f}%   días {len(daily)}")
    return eq, dd


def main():
    print("Descargando y simulando (proxy)...")
    gold = trade_returns("GC=F", 6.0)
    dax  = trade_returns("^GDAXI", 4.0)

    # alinear en calendario común
    all_days = gold.index.union(dax.index)
    g = gold.reindex(all_days, fill_value=0.0)
    d = dax.reindex(all_days, fill_value=0.0)
    combo = g + d                      # ambas en la misma cuenta, 0.4% cada una

    print(f"\nCorrelación de retornos diarios oro vs DAX: {g.corr(d):+.3f}  (0 = descorrelacionadas)\n")
    print("Rendimiento (0.4% riesgo/trade, mismo periodo solapado):")
    stats(g[g.index.isin(gold.index) | (g!=0)], "Oro solo")
    stats(d[d.index.isin(dax.index) | (d!=0)], "DAX solo")
    eqc, ddc = stats(combo, "Oro + DAX (cartera)")

    # ratio return/DD (MAR-like) para ver la mejora
    def mar(daily):
        eq=(1+daily).cumprod(); dd=(eq/eq.cummax()-1).min()
        return (eq.iloc[-1]-1)/abs(dd) if dd<0 else np.nan
    print(f"\nRatio retorno/DD:  oro {mar(g):.2f}   DAX {mar(d):.2f}   CARTERA {mar(combo):.2f}")
    print("Si el DD de la cartera < DD del oro solo, la diversificación funciona: "
          "mismo estilo pero mercados que no caen a la vez -> puedes subir riesgo con más margen.")


if __name__ == "__main__":
    main()
