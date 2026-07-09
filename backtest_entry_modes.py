"""Backtest A/B/C de MODOS DE ENTRADA, segmentado por ATR% (volatilidad del nombre).

Motivación (2026-07-09): en nombres hipervolátiles (p.ej. CIFR, ATR~10% del precio)
la entrada R5 breakout-stop = EMA9 + 0.5·ATR queda tan arriba que:
  - o no dispara, o
  - si dispara, compras justo tras un subidón y te comes el retroceso.
El histórico de CIFR lo confirma: tras +10% en 2 días, el 72% de las veces hay una
caída intradía >=2% al día siguiente (mediana -4%). La pregunta: ¿la regla óptima de
ENTRADA depende del ATR% del nombre? Este backtest lo mide, con la señal fija.

Diseño (la SEÑAL es idéntica en A/B/C; solo cambia la ENTRADA):
  Señal LONG (proxy de setup momentum del pipeline):
    - tendencia:  close > EMA200  y  EMA200 en pendiente positiva (vs -20 barras)
    - momentum:   close hace nuevo máximo de 10 sesiones
    - cooldown:   >= COOLDOWN barras desde la última señal del mismo ticker

  Modos de entrada (orden condicional, ventana FILL_WINDOW barras):
    A. Breakout-stop  (R5 actual): buy-stop en EMA9 + 0.5·ATR
    B. Pullback-limit:             buy-limit en EMA9 (retroceso)
    C. Extension-guard:            = A, pero DESCARTA la señal si el 2-day return
                                   ya es >= EXT_GUARD (no perseguir tras el subidón)

  Salida (idéntica en los tres, para aislar la entrada):
    stop = entrada - STOP_ATR·ATR    target = entrada + TARGET_ATR·ATR
    horizonte HOLD_DAYS; si toca stop primero -> -1R; si target -> +TARGET/STOP R;
    si no, cierre a horizonte. R = (salida-entrada)/(STOP_ATR·ATR).

Métricas por (modo × bucket de ATR%): fill_rate, %abre_rojo, MAE inmediata,
win_rate, R medio (expectativa), %stop, %target, prima de entrada vs cierre señal.

Cautelas: señal proxy (no el pipeline completo), watchlist actual (sesgo de
superviviente), histórico Alpaca/IEX, fills conservadores (gaps al open, stop
antes que target el mismo día). Uso: python backtest_entry_modes.py
"""

import json
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

# Consola Windows (cp1252) no codifica los caracteres de las tablas (─, ≈, ×).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import CONTEXT_DIR
from data.market_data import MarketDataFetcher
from data.indicators import calculate_ema, calculate_atr

# ── Parámetros ────────────────────────────────────────────────────────────────
HISTORY = "1000d"        # histórico por ticker (Alpaca)
COOLDOWN = 5             # barras mínimas entre señales del mismo ticker
FILL_WINDOW = 3          # barras para que rellene la orden de entrada
HOLD_DAYS = 15           # horizonte máximo del swing
STOP_ATR = 1.5           # stop = entrada - STOP_ATR·ATR
TARGET_ATR = 3.0         # target = entrada + TARGET_ATR·ATR  (=> +2R)
STRENGTH_ATR = 0.5       # buffer de fuerza del breakout (R5)
EXT_GUARD = 0.08         # modo C: descarta si 2-day return >= 8%
WARMUP = 210             # barras mínimas antes de la 1ª señal (EMA200 estable)

ATR_BINS = [0, 0.03, 0.05, 0.08, 9.99]
ATR_LABELS = ["<3%", "3-5%", "5-8%", ">=8%"]

R_TARGET = TARGET_ATR / STOP_ATR   # R-multiple del target


def load_watchlist_tickers():
    d = json.load(open("contex/watchlist.json", encoding="utf-8"))
    return sorted(set(d.get("tickers", [])))


def simulate_exit(o, h, l, c, d0, entry, atr):
    """Simula la salida del swing desde la barra de entrada d0. Devuelve (R, outcome)."""
    stop = entry - STOP_ATR * atr
    target = entry + TARGET_ATR * atr
    end = min(d0 + HOLD_DAYS, len(c) - 1)
    for j in range(d0 + 1, end + 1):
        if l[j] <= stop:                    # conservador: stop antes que target
            return -1.0, "stop"
        if h[j] >= target:
            return R_TARGET, "target"
    return (c[end] - entry) / (STOP_ATR * atr), "time"


def try_entry(mode, o, h, l, c, i, ema9_i, atr_i):
    """Busca fill del modo en la ventana. Devuelve (d0, fill) o (None, None)."""
    if mode == "B":
        level = ema9_i                                   # pullback-limit a EMA9
        for j in range(i + 1, min(i + FILL_WINDOW, len(c) - 1) + 1):
            if l[j] <= level:
                fill = min(level, o[j])                  # gap a la baja -> open (mejor)
                return j, fill
        return None, None
    # A y C: breakout-stop
    level = ema9_i + STRENGTH_ATR * atr_i
    for j in range(i + 1, min(i + FILL_WINDOW, len(c) - 1) + 1):
        if h[j] >= level:
            fill = max(level, o[j])                      # gap al alza -> open (peor)
            return j, fill
    return None, None


def main():
    tickers = load_watchlist_tickers()
    print(f"Watchlist: {len(tickers)} tickers. Descargando histórico ({HISTORY}, Alpaca)...")
    fetcher = MarketDataFetcher(CONTEXT_DIR)

    rows = []          # una fila por (señal, modo) con fill
    n_signals = 0
    guarded_out = []   # métricas de A sobre señales que C descartó
    n_dl = 0
    for t in tickers:
        try:
            df = fetcher._fetch_ohlcv_alpaca(t, period=HISTORY, timeframe="day")
        except Exception:
            df = None
        if df is None or len(df) < WARMUP + 30:
            continue
        n_dl += 1
        df = df.sort_index()
        close = df["Close"]
        ema9 = calculate_ema(close, 9).values
        ema200 = calculate_ema(close, 200).values
        atr = calculate_atr(df["High"], df["Low"], close, 14).values
        o, h, l, c = df["Open"].values, df["High"].values, df["Low"].values, close.values
        n = len(c)

        last_sig = -10**9
        for i in range(WARMUP, n - 1):
            if np.isnan(ema200[i]) or np.isnan(atr[i]) or atr[i] <= 0:
                continue
            trend = c[i] > ema200[i] and ema200[i] > ema200[i - 20]
            mom = c[i] >= c[i - 9:i + 1].max()
            if not (trend and mom):
                continue
            if i - last_sig < COOLDOWN:
                continue
            last_sig = i
            n_signals += 1

            atr_pct = atr[i] / c[i]
            two_day = c[i] / c[i - 2] - 1.0 if c[i - 2] > 0 else 0.0
            base = {"ticker": t, "atr_pct": atr_pct, "two_day": two_day}

            for mode in ("A", "B", "C"):
                if mode == "C" and two_day >= EXT_GUARD:
                    continue                              # guard: no opera
                d0, fill = try_entry(mode, o, h, l, c, i, ema9[i], atr[i])
                if d0 is None:
                    rows.append({**base, "mode": mode, "filled": 0})
                    continue
                R, outcome = simulate_exit(o, h, l, c, d0, fill, atr[i])
                imm_lo = l[d0:min(d0 + 3, n)].min()
                rows.append({
                    **base, "mode": mode, "filled": 1,
                    "opened_red": int(c[d0] < fill),
                    "imm_mae": (imm_lo - fill) / fill,
                    "R": R, "hit_stop": int(outcome == "stop"),
                    "hit_target": int(outcome == "target"),
                    "premium": (fill - c[i]) / c[i],
                })
            # A sobre las señales que C descartó (¿acertó C al saltarlas?)
            if two_day >= EXT_GUARD:
                d0, fill = try_entry("A", o, h, l, c, i, ema9[i], atr[i])
                if d0 is not None:
                    R, outcome = simulate_exit(o, h, l, c, d0, fill, atr[i])
                    guarded_out.append(R)

    data = pd.DataFrame(rows)
    print(f"Tickers con datos: {n_dl} | señales: {n_signals} | filas: {len(data)}\n")
    if data.empty:
        print("Sin datos suficientes.")
        return

    data["bucket"] = pd.cut(data["atr_pct"], bins=ATR_BINS, labels=ATR_LABELS)

    def block(sub, mode):
        m = sub[sub["mode"] == mode]
        if len(m) == 0:
            return None
        f = m[m["filled"] == 1]
        fr = len(f) / len(m)
        if len(f) == 0:
            return (len(m), fr, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan, np.nan)
        return (len(m), fr, f["opened_red"].mean(), f["imm_mae"].median(),
                (f["R"] > 0).mean(), f["R"].mean(), f["hit_stop"].mean(),
                f["hit_target"].mean(), f["premium"].mean())

    hdr = f"{'modo':4} {'n':>4} {'fill%':>6} {'rojo%':>6} {'MAEmed':>7} {'win%':>6} {'R_medio':>8} {'stop%':>6} {'tgt%':>6} {'prima':>7}"
    print("=" * 78)
    print("RESULTADO POR BUCKET DE ATR%  (R medio = expectativa por trade, en R)")
    print("=" * 78)
    for lab in ATR_LABELS:
        sub = data[data["bucket"] == lab]
        if sub.empty:
            continue
        print(f"\n── ATR% {lab}  (señales≈{len(sub[sub['mode']=='A'])}) ──")
        print(hdr)
        for mode in ("A", "B", "C"):
            r = block(sub, mode)
            if r is None:
                continue
            n_, fr, red, mae, win, rmean, stp, tgt, prem = r
            def p(x, f="{:>6.0%}"):
                return (f.format(x) if x == x else "   nan")
            print(f"{mode:4} {n_:>4} {p(fr)} {p(red)} "
                  f"{('{:>7.1%}'.format(mae) if mae==mae else '    nan')} {p(win)} "
                  f"{('{:>8.3f}'.format(rmean) if rmean==rmean else '     nan')} "
                  f"{p(stp)} {p(tgt)} {('{:>7.1%}'.format(prem) if prem==prem else '    nan')}")

    # Global
    print("\n" + "=" * 78)
    print("GLOBAL (todos los buckets)")
    print("=" * 78)
    print(hdr)
    for mode in ("A", "B", "C"):
        r = block(data, mode)
        n_, fr, red, mae, win, rmean, stp, tgt, prem = r
        print(f"{mode:4} {n_:>4} {fr:>6.0%} {red:>6.0%} {mae:>7.1%} {win:>6.0%} "
              f"{rmean:>8.3f} {stp:>6.0%} {tgt:>6.0%} {prem:>7.1%}")

    if guarded_out:
        go = np.array(guarded_out)
        print(f"\nModo A sobre señales que C DESCARTÓ (2d>=+{EXT_GUARD:.0%}, n={len(go)}): "
              f"R medio {go.mean():+.3f} | win {(go>0).mean():.0%}  "
              f"→ si R medio<0, el guard acertó al saltarlas.")

    print("\nLeyenda: fill%=órdenes que rellenan · rojo%=cierran bajo entrada el día de fill · "
          f"MAEmed=peor caída intradía (mediana) · R_medio=expectativa (target=+{R_TARGET:.1f}R, stop=-1R) · "
          "prima=cuánto pagas por encima del cierre de la señal.")


if __name__ == "__main__":
    main()
