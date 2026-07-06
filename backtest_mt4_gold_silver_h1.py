"""Backtest walk-forward de estrategias H1 para oro/plata (XAUUSD/XAGUSD), con la
mira puesta en un bot de MT4 — busca, dentro de una familia acotada de estrategias
conocidas, cuál sobrevive a validación fuera de muestra (no solo al backtest
completo, que sobreajusta con facilidad).

Familias probadas (todas con gestión de riesgo fija en múltiplos de ATR):
  A) Cruce de EMAs con filtro de tendencia EMA200 (seguimiento de tendencia).
  B) Ruptura de canal Donchian de N barras, con/sin filtro de tendencia EMA200.
  C) Reversión a la media: RSI extremo + cierre fuera de Bollinger, con/sin
     filtro ADX (evita "pescar" contra tendencias fuertes).

Método (walk-forward anchored):
  1. Descarga ~730 días de velas H1 de GC=F/SI=F (proxy de XAUUSD/XAGUSD —
     futuro COMEX, no CFD spot; hay diferencia de base/roll pero la estructura
     de tendencia/rango es representativa. Yahoo/yfinance topea 1h a 730 días,
     y XAUUSD=X/XAGUSD=X están deslistados en yfinance, de ahí el proxy).
  2. Divide el histórico en folds: una ventana de entrenamiento (IN-SAMPLE)
     que se expande, seguida de una ventana de test (OUT-OF-SAMPLE) que NUNCA
     se usa para elegir parámetros.
  3. En cada fold, para cada familia, barre una rejilla pequeña de parámetros
     sobre el IN-SAMPLE y elige la mejor por expectancy (con mínimo de operaciones
     para evitar ajustar a 3 trades de suerte). Simula esa combinación, sin
     tocarla, sobre el OUT-OF-SAMPLE siguiente.
  4. Solo los resultados OUT-OF-SAMPLE agregados (no el backtest completo)
     se usan para decidir si una familia es candidata a implementarse en MQL4.

Costes: coste ida y vuelta en pb del precio de entrada (spread + slippage
aproximados de un CFD retail; XAUUSD algo más líquido que XAGUSD). Son una
estimación — antes de operar en real, calibrar con el spread/slippage reales
del broker MT4 concreto.

Cautelas: (a) proxy futuro vs spot CFD; (b) solo ~2.4 años de histórico H1
(límite de yfinance) => pocos regímenes de mercado, resultados frágiles;
(c) fills aproximados (stop/target intrabar, sin order book); (d) búsqueda
acotada a 3 familias conocidas, no es garantía de nada, es una criba inicial.

Uso: python backtest_mt4_gold_silver_h1.py
"""

import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import data.market_data  # noqa: F401  (aplica el parche SSL de yfinance como side-effect)
import yfinance as yf

from data.indicators import (
    calculate_ema,
    calculate_atr,
    calculate_rsi,
    calculate_bollinger_bands,
    calculate_adx,
)

INSTRUMENTS = {
    "XAUUSD": {"proxy": "GC=F", "cost_bps": 6.0},
    "XAGUSD": {"proxy": "SI=F", "cost_bps": 12.0},
    # Forex mayores cotizados en USD directo (riesgo de lote mínimo ~$1.2-2.0 en vez
    # de ~$28-36 del oro -> compatible con cuentas pequeñas). cost_bps ~ spread+slippage
    # retail típico (1.5-2 pips) sobre el precio.
    "EURUSD": {"proxy": "EURUSD=X", "cost_bps": 3.0},
    "GBPUSD": {"proxy": "GBPUSD=X", "cost_bps": 3.5},
    "AUDUSD": {"proxy": "AUDUSD=X", "cost_bps": 4.0},
    "NZDUSD": {"proxy": "NZDUSD=X", "cost_bps": 5.0},
}
H1_PERIOD = "730d"
N_FOLDS = 6
INITIAL_TRAIN_FRAC = 0.40
MIN_TRADES_IS = 40          # mínimo de trades en IS para considerar válida una combinación
ATR_PERIOD = 14
TREND_LEN = 200


# --------------------------------------------------------------------------
# Datos
# --------------------------------------------------------------------------

def fetch_h1(ticker: str) -> pd.DataFrame:
    df = yf.Ticker(ticker).history(period=H1_PERIOD, interval="60m", auto_adjust=True)
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


# --------------------------------------------------------------------------
# Señales por familia (usan solo datos hasta la barra i -> sin fuga de futuro)
# --------------------------------------------------------------------------

def signal_ema_cross(df: pd.DataFrame, fast: int, slow: int) -> tuple:
    ema_fast = calculate_ema(df["Close"], fast)
    ema_slow = calculate_ema(df["Close"], slow)
    ema_trend = calculate_ema(df["Close"], TREND_LEN)
    cross_up = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))
    cross_down = (ema_fast < ema_slow) & (ema_fast.shift(1) >= ema_slow.shift(1))
    long_sig = cross_up & (df["Close"] > ema_trend)
    short_sig = cross_down & (df["Close"] < ema_trend)
    return long_sig.fillna(False), short_sig.fillna(False)


def signal_donchian(df: pd.DataFrame, n: int, use_trend_filter: bool) -> tuple:
    high_n = df["High"].rolling(n).max().shift(1)
    low_n = df["Low"].rolling(n).min().shift(1)
    long_sig = df["Close"] > high_n
    short_sig = df["Close"] < low_n
    if use_trend_filter:
        ema_trend = calculate_ema(df["Close"], TREND_LEN)
        long_sig = long_sig & (df["Close"] > ema_trend)
        short_sig = short_sig & (df["Close"] < ema_trend)
    return long_sig.fillna(False), short_sig.fillna(False)


def signal_mean_reversion(df: pd.DataFrame, rsi_os: int, rsi_ob: int, use_adx_filter: bool) -> tuple:
    rsi = calculate_rsi(df["Close"], 14)
    upper, mid, lower, _ = calculate_bollinger_bands(df["Close"], 20, 2.0)
    long_sig = (rsi < rsi_os) & (df["Close"] < lower)
    short_sig = (rsi > rsi_ob) & (df["Close"] > upper)
    if use_adx_filter:
        adx = calculate_adx(df["High"], df["Low"], df["Close"], 14)
        long_sig = long_sig & (adx < 25)
        short_sig = short_sig & (adx < 25)
    return long_sig.fillna(False), short_sig.fillna(False)


FAMILIES = {}

for fast, slow in [(9, 21), (9, 50), (20, 50)]:
    for stop_mult, target_mult in [(1.5, 2.0), (1.5, 3.0)]:
        key = f"A:ema_cross(fast={fast},slow={slow},stop={stop_mult}R,tgt={target_mult}R)"
        FAMILIES[key] = dict(
            family="A_ema_cross",
            signal_fn=lambda df, fast=fast, slow=slow: signal_ema_cross(df, fast, slow),
            stop_mult=stop_mult,
            target_mult=target_mult,
        )

for n in [20, 55, 100]:
    for use_filter in [True, False]:
        for stop_mult, target_mult in [(1.5, 2.0), (1.5, 3.0)]:
            key = f"B:donchian(n={n},trend_filter={use_filter},stop={stop_mult}R,tgt={target_mult}R)"
            FAMILIES[key] = dict(
                family="B_donchian",
                signal_fn=lambda df, n=n, use_filter=use_filter: signal_donchian(df, n, use_filter),
                stop_mult=stop_mult,
                target_mult=target_mult,
            )

for rsi_os, rsi_ob in [(30, 70), (25, 75)]:
    for use_adx in [True, False]:
        for stop_mult, target_mult in [(1.5, 1.5), (1.5, 2.0)]:
            key = f"C:mean_rev(os={rsi_os},ob={rsi_ob},adx_filter={use_adx},stop={stop_mult}R,tgt={target_mult}R)"
            FAMILIES[key] = dict(
                family="C_mean_reversion",
                signal_fn=lambda df, rsi_os=rsi_os, rsi_ob=rsi_ob, use_adx=use_adx: signal_mean_reversion(
                    df, rsi_os, rsi_ob, use_adx
                ),
                stop_mult=stop_mult,
                target_mult=target_mult,
            )


# --------------------------------------------------------------------------
# Motor de simulación (una posición abierta a la vez, entrada a la apertura
# de la barra siguiente a la señal, stop/target intrabar, coste ida y vuelta
# en pb del precio de entrada)
# --------------------------------------------------------------------------

def simulate(df: pd.DataFrame, long_sig: pd.Series, short_sig: pd.Series, atr: pd.Series,
             stop_mult: float, target_mult: float, cost_bps: float) -> list:
    opens = df["Open"].to_numpy()
    highs = df["High"].to_numpy()
    lows = df["Low"].to_numpy()
    closes = df["Close"].to_numpy()
    atr_arr = atr.to_numpy()
    long_arr = long_sig.to_numpy()
    short_arr = short_sig.to_numpy()
    cost_frac = cost_bps / 10000.0

    n = len(df)
    trades = []
    in_pos = False
    direction = 0
    entry_price = stop_price = target_price = 0.0

    for i in range(1, n):
        if in_pos:
            hit_stop = (lows[i] <= stop_price) if direction == 1 else (highs[i] >= stop_price)
            hit_target = (highs[i] >= target_price) if direction == 1 else (lows[i] <= target_price)
            exit_price = None
            if hit_stop:  # conservador: si stop y target caen en la misma barra, gana el stop
                exit_price = stop_price
            elif hit_target:
                exit_price = target_price
            elif i == n - 1:
                exit_price = closes[i]

            if exit_price is not None:
                raw_pnl = (exit_price - entry_price) if direction == 1 else (entry_price - exit_price)
                cost = cost_frac * entry_price
                pnl = raw_pnl - cost
                stop_dist = abs(entry_price - stop_price)
                r_mult = pnl / stop_dist if stop_dist > 0 else 0.0
                trades.append({"direction": direction, "r": r_mult})
                in_pos = False
                direction = 0

        if not in_pos:
            a = atr_arr[i - 1]
            if np.isnan(a) or a <= 0:
                continue
            if long_arr[i - 1]:
                direction = 1
                entry_price = opens[i]
                stop_price = entry_price - stop_mult * a
                target_price = entry_price + target_mult * a
                in_pos = True
            elif short_arr[i - 1]:
                direction = -1
                entry_price = opens[i]
                stop_price = entry_price + stop_mult * a
                target_price = entry_price - target_mult * a
                in_pos = True

    return trades


def trade_stats(trades: list) -> dict:
    if not trades:
        return {"n": 0, "win_rate": 0.0, "avg_r": 0.0, "expectancy_r": 0.0, "profit_factor": 0.0}
    rs = np.array([t["r"] for t in trades])
    wins = rs[rs > 0]
    losses = rs[rs <= 0]
    gross_win = wins.sum() if len(wins) else 0.0
    gross_loss = -losses.sum() if len(losses) else 0.0
    pf = gross_win / gross_loss if gross_loss > 0 else (np.inf if gross_win > 0 else 0.0)
    return {
        "n": len(rs),
        "win_rate": len(wins) / len(rs) * 100.0,
        "avg_r": rs.mean(),
        "expectancy_r": rs.mean(),
        "profit_factor": pf,
    }


# --------------------------------------------------------------------------
# Walk-forward
# --------------------------------------------------------------------------

def run_walk_forward(df: pd.DataFrame, cost_bps: float) -> pd.DataFrame:
    atr = calculate_atr(df["High"], df["Low"], df["Close"], ATR_PERIOD)

    n = len(df)
    train_start_end = int(n * INITIAL_TRAIN_FRAC)
    remaining = n - train_start_end
    oos_len = remaining // N_FOLDS

    precomputed = {}
    for key, spec in FAMILIES.items():
        long_sig, short_sig = spec["signal_fn"](df)
        precomputed[key] = (long_sig, short_sig)

    fold_rows = []
    for fold in range(N_FOLDS):
        train_end = train_start_end + fold * oos_len
        test_end = train_end + oos_len
        if test_end > n:
            break
        train_slice = slice(0, train_end)
        test_slice = slice(train_end, test_end)

        best_key, best_is_stats = None, None
        for key, spec in FAMILIES.items():
            long_sig, short_sig = precomputed[key]
            is_trades = simulate(
                df.iloc[train_slice], long_sig.iloc[train_slice], short_sig.iloc[train_slice],
                atr.iloc[train_slice], spec["stop_mult"], spec["target_mult"], cost_bps,
            )
            stats = trade_stats(is_trades)
            if stats["n"] < MIN_TRADES_IS:
                continue
            if best_is_stats is None or stats["expectancy_r"] > best_is_stats["expectancy_r"]:
                best_key, best_is_stats = key, stats

        if best_key is None:
            continue

        spec = FAMILIES[best_key]
        long_sig, short_sig = precomputed[best_key]
        oos_trades = simulate(
            df.iloc[test_slice], long_sig.iloc[test_slice], short_sig.iloc[test_slice],
            atr.iloc[test_slice], spec["stop_mult"], spec["target_mult"], cost_bps,
        )
        oos_stats = trade_stats(oos_trades)

        fold_rows.append({
            "fold": fold + 1,
            "train_bars": train_end,
            "test_start": df.index[test_slice][0],
            "test_end": df.index[test_slice][-1],
            "chosen_strategy": best_key,
            "chosen_family": spec["family"],
            "is_n": best_is_stats["n"],
            "is_expectancy_r": round(best_is_stats["expectancy_r"], 3),
            "oos_n": oos_stats["n"],
            "oos_win_rate": round(oos_stats["win_rate"], 1),
            "oos_expectancy_r": round(oos_stats["expectancy_r"], 3),
            "oos_profit_factor": round(oos_stats["profit_factor"], 2) if np.isfinite(oos_stats["profit_factor"]) else float("inf"),
        })

    return pd.DataFrame(fold_rows)


def main():
    all_results = {}
    for name, cfg in INSTRUMENTS.items():
        print(f"\n=== {name} (proxy {cfg['proxy']}, coste {cfg['cost_bps']} pb ida y vuelta) ===")
        df = fetch_h1(cfg["proxy"])
        print(f"  barras H1: {len(df)}  [{df.index.min()} -> {df.index.max()}]")
        results = run_walk_forward(df, cfg["cost_bps"])
        all_results[name] = results
        if results.empty:
            print("  (sin folds válidos — histórico insuficiente para el mínimo de trades exigido)")
            continue
        print(results.to_string(index=False))

        oos_r_total = results["oos_expectancy_r"].to_numpy() * results["oos_n"].to_numpy()
        oos_n_total = results["oos_n"].sum()
        agg_expectancy = oos_r_total.sum() / oos_n_total if oos_n_total > 0 else float("nan")
        pct_folds_positive = (results["oos_expectancy_r"] > 0).mean() * 100
        print(f"\n  OOS agregado: {oos_n_total} trades, expectancy {agg_expectancy:.3f}R/trade, "
              f"{pct_folds_positive:.0f}% de folds con expectancy OOS positiva")

    print("\n=== Resumen ===")
    for name, results in all_results.items():
        if results.empty:
            print(f"  {name}: sin datos suficientes")
            continue
        families_won = results["chosen_family"].value_counts().to_dict()
        n_positive = (results["oos_expectancy_r"] > 0).sum()
        print(f"  {name}: {n_positive}/{len(results)} folds OOS positivos. Familias elegidas por fold: {families_won}")


if __name__ == "__main__":
    main()
