"""Backtest 'carril de breakout por volumen' — ¿comprar la fuerza (gap-and-go con
volumen) bate a 'esperar pullback'?

Motivación: el MarketScanner penaliza -2.0 todo lo que sube >=5% en el día
(extended_intraday, market_scanner.py:175) y lo aparca en 'esperar pullback'.
Pero R5 ya falsificó "no perseguir extensión" ("la extensión no es el problema,
la debilidad lo es"). Aquí probamos, sobre histórico, si el chase de un gap fuerte
GANA o PIERDE frente a esperar el pullback — y, sobre todo, si el VOLUMEN relativo
separa los gaps que siguen corriendo de los que se desinflan (la variable que el
escáner hoy ignora del todo).

Método: para cada ticker del watchlist, baja ~2 años de velas diarias, calcula
EMA9/EMA21/ATR/vol_avg20/52w-high SIN mirar al futuro, y localiza 'días señal'
(retorno diario >= umbral). Para cada señal mide:
  - CHASE: compra al cierre del día señal, mantiene FWD_DAYS días.
  - PULLBACK: espera PULLBACK_FILL_DAYS a que el low toque la EMA9; si rellena,
    compra ahí; si NO rellena (los más fuertes), te lo pierdes -> retorno 0
    (coste de oportunidad de la regla de pullback).
Agrupa por bucket de VOLUMEN relativo para ver si el carril de alto volumen
realmente compensa el riesgo de gap-and-fade.

Cautelas: el watchlist son ganadores de HOY -> SESGO DE SUPERVIVENCIA (infla el
"sigue corriendo"); precios ajustados; fills aproximados (chase=cierre del día,
pullback=toque del low); pocos regímenes. Señal direccional, no evangelio.

Uso: python backtest_breakout_lane.py
"""

import sys

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")  # emojis/acentos en consola Windows
except Exception:
    pass

from config import CONTEXT_DIR
from data.market_data import MarketDataFetcher
from data.indicators import calculate_ema, calculate_atr

HIST_PERIOD = "2y"          # histórico por ticker
FWD_DAYS = 5                # ventana de hold (días de mercado)
PULLBACK_FILL_DAYS = 3      # días que damos a que rellene el pullback a EMA9
GAP_THRESHOLDS = [3.0, 5.0, 8.0]   # umbrales de 'día señal' a barrer
MAIN_GAP = 5.0             # umbral ancla = el del escáner (extended_intraday)
VOL_BUCKETS = [(0.0, 1.5), (1.5, 3.0), (3.0, 5.0), (5.0, 1e9)]
VOL_LABELS = ["<1.5x", "1.5-3x", "3-5x", ">5x"]
NEAR_HIGH_PCT = 0.85       # = NEAR_52W_HIGH_PCT del config


def build_observations(prices: dict, gap_thr: float) -> pd.DataFrame:
    """Reconstruye todas las señales (retorno diario >= gap_thr) en el histórico y
    mide chase vs pullback forward para cada una. Indicadores calculados hasta el
    día señal (sin fuga de futuro)."""
    rows = []
    for ticker, df in prices.items():
        if df is None or len(df) < 60:
            continue
        close = df["Close"]
        high = df["High"]
        low = df["Low"]
        vol = df["Volume"]

        ret_day = close.pct_change() * 100.0
        ema9 = calculate_ema(close, 9)
        atr = calculate_atr(high, low, close, 14)
        vol_avg20 = vol.rolling(20).mean()
        vol_ratio = vol / vol_avg20
        high_252 = high.rolling(252, min_periods=60).max()

        n = len(df)
        # dejamos margen al inicio (indicadores templados) y al final (datos forward)
        for i in range(30, n - FWD_DAYS - 1):
            r = ret_day.iloc[i]
            if not (r >= gap_thr):
                continue
            a = atr.iloc[i]
            e9 = ema9.iloc[i]
            vr = vol_ratio.iloc[i]
            if not (np.isfinite(a) and a > 0 and np.isfinite(e9) and np.isfinite(vr)):
                continue

            entry = float(close.iloc[i])
            fwd = df.iloc[i + 1 : i + 1 + FWD_DAYS]
            if fwd.empty:
                continue
            f_close = fwd["Close"].values
            f_low = fwd["Low"].values
            f_high = fwd["High"].values

            chase_ret = (f_close[-1] - entry) / entry
            mae = (f_low.min() - entry) / entry
            mfe = (f_high.max() - entry) / entry

            # Pullback a EMA9 dentro de la ventana de relleno
            pb_level = float(e9)
            pb_win = df.iloc[i + 1 : i + 1 + PULLBACK_FILL_DAYS]
            pb_filled = bool((pb_win["Low"].values <= pb_level).any()) if not pb_win.empty else False
            pb_ret = (f_close[-1] - pb_level) / pb_level if pb_filled else np.nan
            pb_eff = pb_ret if pb_filled else 0.0  # si no rellena, no operas (0)

            hi252 = high_252.iloc[i]
            near_high = bool(np.isfinite(hi252) and entry >= hi252 * NEAR_HIGH_PCT)

            rows.append({
                "ticker": ticker,
                "gap_pct": float(r),
                "vol_ratio": float(vr),
                "ext_atr": (entry - float(e9)) / float(a),
                "near_high": near_high,
                "chase_ret": chase_ret,
                "pb_filled": pb_filled,
                "pb_eff": float(pb_eff),
                "mae": mae,
                "mfe": mfe,
            })
    return pd.DataFrame(rows)


def vol_bucket(vr: float) -> str:
    for (lo, hi), lab in zip(VOL_BUCKETS, VOL_LABELS):
        if lo <= vr < hi:
            return lab
    return VOL_LABELS[-1]


def main():
    fetcher = MarketDataFetcher(CONTEXT_DIR)
    universe = fetcher.get_universe()
    print(f"Universo: {len(universe)} tickers. Descargando {HIST_PERIOD} diario (Alpaca primario)...")

    prices = {}
    for i, t in enumerate(universe):
        try:
            df = fetcher.fetch_ohlcv(t, period=HIST_PERIOD, timeframe="day")
            if df is not None and not df.empty:
                idx = pd.to_datetime(df.index)
                if idx.tz is not None:
                    idx = idx.tz_localize(None)
                df = df.copy()
                df.index = idx.normalize()
                prices[t.upper()] = df
        except Exception:
            pass
        if (i + 1) % 20 == 0:
            print(f"  ... {i + 1}/{len(universe)}")
    print(f"Con histórico válido: {len(prices)} tickers\n")

    # ── Sensibilidad: nº de señales por umbral de gap ─────────────────────────
    print("Señales por umbral de gap diario:")
    obs_by_thr = {}
    for thr in GAP_THRESHOLDS:
        o = build_observations(prices, thr)
        obs_by_thr[thr] = o
        print(f"  gap >= {thr:>4.1f}% : {len(o):>4} señales")
    print()

    df = obs_by_thr[MAIN_GAP]
    if df.empty:
        print("Sin observaciones al umbral ancla. Fin.")
        return

    print(f"=== ANCLA: gap diario >= {MAIN_GAP}% (= extended_intraday del escáner), "
          f"hold {FWD_DAYS}d ===")
    print(f"Total señales: {len(df)}\n")

    # ── Tabla principal: por bucket de VOLUMEN ────────────────────────────────
    # MEDIANAS como métrica primaria (los retornos son fat-tailed: la media la
    # secuestran 2-3 pennies que se multiplican). %chase+ = win-rate del chase.
    df["vbucket"] = df["vol_ratio"].apply(vol_bucket)
    print("Por bucket de VOLUMEN relativo (la variable que el escáner ignora):")
    print("MEDIANAS (robusto a outliers) | %win = % chase positivo")
    print(f"{'volumen':8} {'n':>4} {'chase_md':>9} {'pb_ef_md':>9} {'edge_md':>8} "
          f"{'%win':>6} {'pb_rell':>8} {'chase_avg':>10} {'MAE_md':>8}")
    for lab in VOL_LABELS:
        g = df[df["vbucket"] == lab]
        if len(g) == 0:
            continue
        chase_md = g["chase_ret"].median()
        pbeff_md = g["pb_eff"].median()
        edge_md = chase_md - pbeff_md
        fill = g["pb_filled"].mean()
        pos = (g["chase_ret"] > 0).mean()
        print(f"{lab:8} {len(g):>4} {chase_md:>+9.1%} {pbeff_md:>+9.1%} {edge_md:>+8.1%} "
              f"{pos:>6.0%} {fill:>8.0%} {g['chase_ret'].mean():>+10.1%} {g['mae'].median():>+8.1%}")

    # ── Breakout real (cerca de máximos 52s) vs no, en alto volumen ───────────
    hv = df[df["vol_ratio"] >= 3.0]
    print(f"\nAlto volumen (>=3x), n={len(hv)}:")
    for label, sub in [("cerca 52s-high", hv[hv["near_high"]]),
                       ("lejos de 52s-high", hv[~hv["near_high"]])]:
        if len(sub) == 0:
            continue
        print(f"  {label:18} n={len(sub):>3}: chase {sub['chase_ret'].mean():+.1%} | "
              f"pb_efic {sub['pb_eff'].mean():+.1%} | pb_rellena {sub['pb_filled'].mean():.0%} | "
              f"%chase+ {(sub['chase_ret'] > 0).mean():.0%}")

    # ── Veredicto (sobre MEDIANAS) ────────────────────────────────────────────
    print("\n--- LECTURA (sobre medianas) ---")
    lowv = df[df["vol_ratio"] < 1.5]
    highv = df[df["vol_ratio"] >= 3.0]
    if len(lowv) >= 3:
        c, p = lowv["chase_ret"].median(), lowv["pb_eff"].median()
        print(f"Bajo volumen (<1.5x):  chase_md {c:+.1%} vs pb_efic_md {p:+.1%}  -> "
              f"{'chase gana' if c > p else 'pullback gana (esperar OK)'}")
    if len(highv) >= 3:
        c, p = highv["chase_ret"].median(), highv["pb_eff"].median()
        miss = 1 - highv["pb_filled"].mean()
        print(f"Alto volumen (>=3x):   chase_md {c:+.1%} vs pb_efic_md {p:+.1%}  -> "
              f"{'CHASE gana (el carril de volumen compensa)' if c > p else 'pullback aguanta'}")
        print(f"  ...y el {miss:.0%} de esos gaps de alto volumen NUNCA dan el pullback "
              f"(te los pierdes enteros con la regla actual).")
    print("\n[!] Sesgo de supervivencia (watchlist = ganadores de hoy): trata los "
          "retornos como cota superior optimista, no como expectativa real.")


if __name__ == "__main__":
    main()
