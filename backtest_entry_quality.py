"""Backtest 'chase vs pullback' — calibra ENTRY_EXTENSION_ATR_MAX (regla R5).

Para cada recomendación LARGA (BUY/STRONG BUY) de los daily_state:
  1. Reconstruye la extensión en ATR al momento de la rec:  (price - ema9)/atr.
  2. Trae precios forward (yfinance) de los siguientes días de mercado.
  3. Mide el comportamiento inmediato comprando a mercado ("chase") y compara con
     una entrada simulada en pullback (a la EMA9), incluyendo si habría rellenado.

Objetivo: ver si "extendido" predice peor comportamiento inmediato (abrir en rojo,
mayor excursión adversa, más stops) y dónde está el umbral que mejor separa →
calibrar ENTRY_EXTENSION_ATR_MAX con datos, no a ojo.

Cautelas: muestra pequeña, un solo régimen (may-jun), precios yfinance ajustados,
fills aproximados (chase = cierre del día; pullback = toque intradía del low).
Uso: python backtest_entry_quality.py
"""

import glob
import json
from collections import defaultdict

import numpy as np
import pandas as pd

from config import CONTEXT_DIR
from data.market_data import MarketDataFetcher
from utils.risk_policy import classify_tier

FWD_DAYS = 5          # ventana forward (días de mercado)
PULLBACK_FILL_DAYS = 3  # días para que la entrada en pullback rellene


def load_long_recs():
    recs = []
    for f in sorted(glob.glob("contex/daily_state_*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        date = d.get("date")
        for c in d.get("candidates", []):
            if c.get("recommendation") not in ("BUY", "STRONG BUY"):
                continue
            ta = c.get("ta_data") or {}
            if ta.get("direction") != "long":
                continue
            ind = ta.get("indicators") or {}
            rd = c.get("risk_data") or {}
            price = ind.get("price") or 0
            ema9 = ind.get("ema9") or 0
            atr = ind.get("atr_14") or 0
            entry = rd.get("entry_price") or price
            if not (price and ema9 and atr and entry):
                continue
            recs.append({
                "date": pd.Timestamp(date),
                "ticker": c["ticker"].upper(),
                "rec": c["recommendation"],
                "price": price, "ema9": ema9, "atr": atr,
                "entry": entry,
                "stop": rd.get("stop_loss") or 0,
                "t1": rd.get("target_1") or 0,
                "ext_atr": (price - ema9) / atr,
                "tier": classify_tier(c["ticker"])[0],
            })
    return recs


def fetch_prices(tickers):
    """OHLCV diario (90d) por ticker vía el fetcher del proyecto (Alpaca primario).
    Normaliza el índice a fechas tz-naive para comparar con las fechas de las recs."""
    fetcher = MarketDataFetcher(CONTEXT_DIR)
    data = {}
    for t in sorted(tickers):
        try:
            df = fetcher.fetch_ohlcv(t, period="90d", timeframe="day")
            if df is not None and not df.empty:
                idx = pd.to_datetime(df.index)
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                df = df.copy()
                df.index = idx.normalize()
                data[t] = df
        except Exception:
            pass
    return data


def forward_metrics(rec, df):
    """Métricas forward para una rec. Devuelve dict o None si no hay datos."""
    idx = df.index[df.index > rec["date"]]
    if len(idx) == 0:
        return None
    fwd = df.loc[idx][:FWD_DAYS]
    if fwd.empty:
        return None
    fill = rec["entry"]
    lows = fwd["Low"].values
    highs = fwd["High"].values
    closes = fwd["Close"].values

    mae = (lows.min() - fill) / fill                 # excursión adversa máx
    mfe = (highs.max() - fill) / fill                # excursión favorable máx
    ret1 = (closes[0] - fill) / fill
    retN = (closes[-1] - fill) / fill
    opened_red = closes[0] < fill                    # cierra en rojo a +1d
    hit_stop = bool((lows <= rec["stop"]).any()) if rec["stop"] else False
    hit_t1 = bool((highs >= rec["t1"]).any()) if rec["t1"] else False

    # Contrafactual: entrada en pullback a la EMA9 (¿rellena? ¿qué hace luego?)
    pb_level = rec["ema9"]
    pb_window = fwd[:PULLBACK_FILL_DAYS]
    pb_filled = bool((pb_window["Low"].values <= pb_level).any())
    pb_ret = np.nan
    if pb_filled:
        # tras rellenar, retorno a cierre de la ventana desde el nivel de pullback
        pb_ret = (closes[-1] - pb_level) / pb_level

    return {
        "ext_atr": rec["ext_atr"], "tier": rec["tier"], "mae": mae, "mfe": mfe,
        "ret1": ret1, "retN": retN, "opened_red": opened_red,
        "hit_stop": hit_stop, "hit_t1": hit_t1,
        "pb_filled": pb_filled, "pb_ret": pb_ret,
        "chase_ret": retN,
    }


def main():
    recs = load_long_recs()
    print(f"Recomendaciones largas (BUY/STRONG BUY): {len(recs)}")
    tickers = {r["ticker"] for r in recs}
    print(f"Descargando {len(tickers)} tickers (Alpaca, 90d diario)...")
    prices = fetch_prices(tickers)

    rows = []
    for r in recs:
        df = prices.get(r["ticker"])
        if df is None:
            continue
        m = forward_metrics(r, df)
        if m:
            rows.append(m)
    df = pd.DataFrame(rows)
    print(f"Observaciones con precio forward: {len(df)}\n")
    if df.empty:
        return

    # ── Correlaciones extensión → comportamiento inmediato ────────────────────
    print("Correlación  extensión(ATR) →")
    for col, label in [("mae", "MAE (excursión adversa)"), ("ret1", "retorno +1d"),
                       ("retN", f"retorno +{FWD_DAYS}d")]:
        print(f"  {label:28} r = {df['ext_atr'].corr(df[col]):+.3f}")

    # ── Tabla por buckets de extensión ────────────────────────────────────────
    bins = [-99, 0.0, 0.5, 1.0, 1.5, 2.0, 99]
    labels = ["<0", "0-0.5", "0.5-1", "1-1.5", "1.5-2", ">2"]
    df["bucket"] = pd.cut(df["ext_atr"], bins=bins, labels=labels)
    print(f"\n{'ext(ATR)':8} {'n':>3} {'abre_rojo':>9} {'MAE_med':>8} {'ret+'+str(FWD_DAYS)+'d':>8} {'%stop':>6} {'%T1':>6}")
    for lab in labels:
        g = df[df["bucket"] == lab]
        if len(g) == 0:
            continue
        print(f"{lab:8} {len(g):>3} {g['opened_red'].mean():>8.0%} "
              f"{g['mae'].mean():>8.1%} {g['retN'].mean():>8.1%} "
              f"{g['hit_stop'].mean():>6.0%} {g['hit_t1'].mean():>6.0%}")

    # ── Chase vs Pullback en el subconjunto EXTENDIDO (>umbral candidato) ──────
    print("\nChase vs Pullback (subconjunto extendido):")
    for thr in [0.75, 1.0, 1.25, 1.5]:
        ext = df[df["ext_atr"] > thr]
        if len(ext) < 3:
            continue
        chase = ext["chase_ret"].mean()
        filled = ext[ext["pb_filled"]]
        fill_rate = ext["pb_filled"].mean()
        pb = filled["pb_ret"].mean() if len(filled) else float("nan")
        # pullback con coste de oportunidad: 0 si no rellena (no operas)
        pb_eff = ext.apply(lambda x: x["pb_ret"] if x["pb_filled"] else 0.0, axis=1).mean()
        print(f"  umbral >{thr} ATR  (n={len(ext):>2}): "
              f"chase ret+{FWD_DAYS}d {chase:+.1%} | "
              f"pullback rellena {fill_rate:.0%}, ret {pb:+.1%} | "
              f"pullback_efectivo {pb_eff:+.1%}")

    # ── Split por tier en el subconjunto extendido (>1 ATR) ───────────────────
    print("\nExtendido (>1 ATR) por tier:")
    ext = df[df["ext_atr"] > 1.0]
    print(f"{'tier':5} {'n':>3} {'abre_rojo':>9} {'MAE_med':>8} {'ret+'+str(FWD_DAYS)+'d':>8} {'%stop':>6} {'%T1':>6}")
    for tier in ["A", "B", "C"]:
        g = ext[ext["tier"] == tier]
        if len(g) == 0:
            continue
        print(f"{tier:5} {len(g):>3} {g['opened_red'].mean():>8.0%} "
              f"{g['mae'].mean():>8.1%} {g['retN'].mean():>8.1%} "
              f"{g['hit_stop'].mean():>6.0%} {g['hit_t1'].mean():>6.0%}")

    # ── Comparativa débil vs extendido (la señal que sí aparece) ───────────────
    weak = df[df["ext_atr"] <= 0.5]
    strong = df[df["ext_atr"] > 0.5]
    print(f"\nDébil (<=0.5 ATR, n={len(weak)}):    abre_rojo {weak['opened_red'].mean():.0%} | "
          f"%stop {weak['hit_stop'].mean():.0%} | ret+{FWD_DAYS}d {weak['retN'].mean():+.1%}")
    print(f"Con fuerza (>0.5 ATR, n={len(strong)}): abre_rojo {strong['opened_red'].mean():.0%} | "
          f"%stop {strong['hit_stop'].mean():.0%} | ret+{FWD_DAYS}d {strong['retN'].mean():+.1%}")


if __name__ == "__main__":
    main()
