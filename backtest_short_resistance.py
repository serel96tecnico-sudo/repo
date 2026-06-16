"""Backtest 'fade de resistencia sin fuerza' — valida la regla candidata R6.

Hipótesis del operador: "un valor que se acerca a una resistencia SIN FUERZA se
puede cortar en la propia resistencia". El espejo de R5 (en largos exigimos fuerza;
en cortos fadeamos la debilidad que llega a resistencia).

Para cada ticker del universo y cada día histórico:
  1. Resistencia R = nivel previo establecido (máximo de High en una ventana pasada,
     con hueco para que sea un nivel "conocido" antes del día evaluado — sin lookahead).
  2. Setup = el precio se acerca/toca R por primera vez (rally hacia la resistencia).
  3. Mide la FUERZA del acercamiento (RSI, MACD, volumen, posición vs EMA50).
  4. Simula un corto a límite EN R y trae precios forward.
  5. Outcome: ¿rechaza (cae → gana el corto) o ROMPE al alza (squeeze → pierde)?

Objetivo: ver si "sin fuerza" predice rechazo (corto bueno) y "con fuerza" predice
ruptura/squeeze (corto malo), y cuánto separa el filtro → decidir si R6 es real o
una máquina de squeezes ANTES de cablear nada al pipeline.

Cautelas: muestra de un solo régimen (los ~90d disponibles), precios ajustados,
resistencia por proxy de máximos (no pivotes confirmados), fills aproximados (el
corto se asume relleno el día que el High toca R; outcome desde T+1, conservador).
Uso: python backtest_short_resistance.py
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

from config import CONTEXT_DIR
from data.market_data import MarketDataFetcher

# ── Parámetros del setup ──────────────────────────────────────────────────────
LOOKBACK = 20          # ventana para el nivel de resistencia (días)
GAP = 2                # hueco: R se mide hasta T-GAP (nivel establecido, no de hoy)
NEAR_PCT = 0.015       # "cerca de R": High dentro del 1.5% por debajo de R
FWD_DAYS = 5           # ventana forward (días de mercado)
STOP_PCT = 0.03        # stop del corto: 3% por encima de R (≈ squeeze)
RR_T1 = 1.6            # objetivo T1 a 1.6R por debajo de R
MIN_PRICE = 3.0        # descarta sub-penny ruido


def _ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def _rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _atr(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def enrich(df):
    df = df.copy()
    df["ema9"] = _ema(df["Close"], 9)
    df["ema21"] = _ema(df["Close"], 21)
    df["ema50"] = _ema(df["Close"], 50)
    df["rsi"] = _rsi(df["Close"])
    macd = _ema(df["Close"], 12) - _ema(df["Close"], 26)
    df["macd_hist"] = macd - _ema(macd, 9)
    df["atr"] = _atr(df)
    df["vol_sma20"] = df["Volume"].rolling(20).mean()
    # Resistencia establecida: máx High en [T-LOOKBACK-GAP, T-GAP]  (sin incluir hoy)
    df["resist"] = df["High"].shift(GAP).rolling(LOOKBACK).max()
    return df


def find_setups(df):
    """Devuelve filas (dict) donde el precio se acerca por primera vez a la resistencia."""
    setups = []
    for i in range(LOOKBACK + GAP + 2, len(df) - 1):
        row, prev = df.iloc[i], df.iloc[i - 1]
        R = row["resist"]
        if not R or np.isnan(R) or row["Close"] < MIN_PRICE:
            continue
        zone_lo = R * (1 - NEAR_PCT)
        # Setup: High de hoy entra en la zona [R*(1-near), R*1.01] y ayer NO estaba ahí
        if not (zone_lo <= row["High"] <= R * 1.01):
            continue
        if prev["High"] >= zone_lo:        # ya estaba pegado ayer → no recontar
            continue
        if any(np.isnan(row[c]) for c in ("ema50", "rsi", "macd_hist", "vol_sma20", "atr")):
            continue
        # ── Fuerza del acercamiento (componentes de "sin fuerza") ─────────────
        below_ema50 = row["Close"] < row["ema50"]          # contexto bajista
        macd_neg = row["macd_hist"] <= 0                   # momentum negativo
        vol_light = row["Volume"] < row["vol_sma20"]       # acercamiento sin volumen
        rsi_soft = row["rsi"] < 55                         # sin empuje alcista
        weakness = int(below_ema50) + int(macd_neg) + int(vol_light) + int(rsi_soft)
        setups.append({
            "i": i, "R": R, "rsi": row["rsi"], "macd_hist": row["macd_hist"],
            "below_ema50": below_ema50, "macd_neg": macd_neg,
            "vol_light": vol_light, "rsi_soft": rsi_soft, "weakness": weakness,
        })
    return setups


def forward_short(df, s):
    """Outcome de un corto a límite en R (fill el día del setup; outcome desde T+1)."""
    i, R = s["i"], s["R"]
    fwd = df.iloc[i + 1:i + 1 + FWD_DAYS]
    if fwd.empty:
        return None
    highs, lows, closes = fwd["High"].values, fwd["Low"].values, fwd["Close"].values
    stop = R * (1 + STOP_PCT)
    t1 = R * (1 - RR_T1 * STOP_PCT)
    mae = (highs.max() - R) / R          # excursión EN CONTRA (sube) — adverso al corto
    mfe = (R - lows.min()) / R           # excursión A FAVOR (baja)
    retN = (R - closes[-1]) / R          # P/L del corto al cierre de la ventana
    broke_out = bool(highs.max() >= stop)           # squeeze: rompió 3% sobre R
    hit_t1 = bool(lows.min() <= t1)                 # alcanzó objetivo antes
    rejected = closes[-1] < R                        # rechazó (cierra por debajo)
    return {**s, "mae": mae, "mfe": mfe, "retN": retN,
            "broke_out": broke_out, "hit_t1": hit_t1, "rejected": rejected}


def load_universe():
    wl = CONTEXT_DIR / "watchlist.json"
    data = json.loads(wl.read_text(encoding="utf-8"))
    entries = data.get("entries")
    if entries:
        return [e["ticker"] for e in entries]
    return data.get("tickers", [])


def main():
    tickers = load_universe()
    print(f"Universo: {len(tickers)} tickers. Descargando OHLCV 90d (Alpaca)...")
    fetcher = MarketDataFetcher(CONTEXT_DIR)
    rows = []
    for t in tickers:
        try:
            df = fetcher.fetch_ohlcv(t, period="90d", timeframe="day")
            if df is None or df.empty or len(df) < LOOKBACK + GAP + FWD_DAYS + 5:
                continue
            df = enrich(df)
            for s in find_setups(df):
                m = forward_short(df, s)
                if m:
                    rows.append({**m, "ticker": t})
        except Exception:
            continue

    df = pd.DataFrame(rows)
    print(f"Setups 'rally hacia resistencia': {len(df)}\n")
    if df.empty:
        print("Sin setups — revisa parámetros.")
        return

    base_reject = df["rejected"].mean()
    base_squeeze = df["broke_out"].mean()
    print(f"Base rate global:  rechaza {base_reject:.0%} | squeeze {base_squeeze:.0%} | "
          f"ret+{FWD_DAYS}d medio {df['retN'].mean():+.1%}\n")

    # ── Por nivel de debilidad del acercamiento (0=fuerte … 4=muy débil) ──────
    print("Por DEBILIDAD del acercamiento (la hipótesis):")
    print(f"{'weakness':9} {'n':>3} {'rechaza':>8} {'squeeze':>8} {'ret+'+str(FWD_DAYS)+'d':>8} {'MAE_med':>8} {'%T1':>6}")
    for w in [0, 1, 2, 3, 4]:
        g = df[df["weakness"] == w]
        if len(g) == 0:
            continue
        print(f"{w:<9} {len(g):>3} {g['rejected'].mean():>8.0%} {g['broke_out'].mean():>8.0%} "
              f"{g['retN'].mean():>8.1%} {g['mae'].mean():>8.1%} {g['hit_t1'].mean():>6.0%}")

    # ── Aporte de cada filtro individual ──────────────────────────────────────
    print("\nAporte de cada componente (True = condición de debilidad presente):")
    print(f"{'filtro':12} {'n_True':>7} {'rechaza_T':>10} {'rechaza_F':>10} {'ret_T':>7} {'ret_F':>7}")
    for col in ["below_ema50", "macd_neg", "vol_light", "rsi_soft"]:
        gt, gf = df[df[col]], df[~df[col]]
        if len(gt) == 0 or len(gf) == 0:
            continue
        print(f"{col:12} {len(gt):>7} {gt['rejected'].mean():>10.0%} {gf['rejected'].mean():>10.0%} "
              f"{gt['retN'].mean():>7.1%} {gf['retN'].mean():>7.1%}")

    # ── Veredicto: 'sin fuerza' (>=3) vs 'con fuerza' (<=1) ────────────────────
    sinf = df[df["weakness"] >= 3]
    conf = df[df["weakness"] <= 1]
    print(f"\nSIN FUERZA (weakness>=3, n={len(sinf)}): rechaza {sinf['rejected'].mean():.0%} | "
          f"squeeze {sinf['broke_out'].mean():.0%} | ret+{FWD_DAYS}d {sinf['retN'].mean():+.1%} | "
          f"%T1 {sinf['hit_t1'].mean():.0%}")
    print(f"CON FUERZA (weakness<=1, n={len(conf)}): rechaza {conf['rejected'].mean():.0%} | "
          f"squeeze {conf['broke_out'].mean():.0%} | ret+{FWD_DAYS}d {conf['retN'].mean():+.1%} | "
          f"%T1 {conf['hit_t1'].mean():.0%}")
    print("\nLectura: si SIN FUERZA rechaza más / hace squeeze menos / ret más positivo "
          "que CON FUERZA, el filtro tiene edge. Si no, R6 sería una máquina de squeezes.")


if __name__ == "__main__":
    main()
