"""Búsqueda de la estrategia nº2 para FTMO: misma criba walk-forward que validó
el oro (backtest_mt4_gold_silver_h1.py — familias A/B/C, folds IS->OOS), pero
sobre instrumentos DISTINTOS del oro (índices, cripto, petróleo) para diversificar.
Deja que los datos elijan la mejor por expectancy OUT-OF-SAMPLE.

Reutiliza fetch_h1 / run_walk_forward / FAMILIES del backtest del oro.
Uso: python backtest_strategy2_search.py
"""
import sys
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from backtest_mt4_gold_silver_h1 import fetch_h1, run_walk_forward

# Proxies yfinance (H1, 730d). Futuros = ~24h, mejor proxy de CFD que los ETF.
INSTRUMENTS = {
    "US100 (Nasdaq)": {"proxy": "NQ=F", "cost_bps": 3.0},
    "US500 (S&P500)": {"proxy": "ES=F", "cost_bps": 3.0},
    "US30 (Dow)":     {"proxy": "YM=F", "cost_bps": 3.5},
    "GER40 (DAX)":    {"proxy": "^GDAXI", "cost_bps": 4.0},
    "BTC":            {"proxy": "BTC-USD", "cost_bps": 6.0},
    "Oro (ref)":      {"proxy": "GC=F", "cost_bps": 6.0},   # referencia: ya es la nº1
}


def main():
    summary = []
    for name, cfg in INSTRUMENTS.items():
        print(f"\n=== {name} (proxy {cfg['proxy']}, coste {cfg['cost_bps']} pb) ===")
        try:
            df = fetch_h1(cfg["proxy"])
        except Exception as e:
            print(f"  fallo al descargar: {e}")
            continue
        if df is None or len(df) < 500:
            print("  histórico insuficiente")
            continue
        print(f"  barras H1: {len(df)}  [{df.index.min()} -> {df.index.max()}]")
        res = run_walk_forward(df, cfg["cost_bps"])
        if res.empty:
            print("  sin folds válidos")
            continue
        oos_r = (res["oos_expectancy_r"].to_numpy() * res["oos_n"].to_numpy())
        oos_n = res["oos_n"].sum()
        agg = oos_r.sum() / oos_n if oos_n > 0 else float("nan")
        pct_pos = (res["oos_expectancy_r"] > 0).mean() * 100
        fam = res["chosen_family"].value_counts().to_dict()
        print(res[["fold", "chosen_family", "oos_n", "oos_win_rate", "oos_expectancy_r"]].to_string(index=False))
        print(f"  >> OOS agregado: {oos_n} trades, {agg:+.3f}R/trade, {pct_pos:.0f}% folds positivos | familias: {fam}")
        summary.append((name, oos_n, agg, pct_pos, fam))

    print("\n" + "="*70)
    print("RANKING (por expectancy OOS agregada)")
    print("="*70)
    summary.sort(key=lambda x: (x[2] if x[2]==x[2] else -9), reverse=True)
    print(f"{'instrumento':18}{'trades':>8}{'R/trade':>9}{'% folds+':>10}  familia dominante")
    for name, n, agg, pct, fam in summary:
        dom = max(fam, key=fam.get) if fam else "-"
        print(f"{name:18}{n:>8}{agg:>+9.3f}{pct:>9.0f}%  {dom}")
    print("\nBuen nº2 = R/trade OOS claramente positivo, % folds+ alto, y a poder ser "
          "familia DISTINTA del oro (que es B_donchian) para descorrelacionar.")


if __name__ == "__main__":
    main()
