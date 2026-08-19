"""Rendimiento REAL del scoring: simula qué habría pasado si TODAS las recomendaciones
accionables (BUY/STRONG BUY/SELL/STRONG SELL) que el pipeline propuso -- mañana y tarde,
desde el primer daily_state disponible hasta hoy -- se hubieran ejecutado con el tamaño de
posición que el propio risk_manager calculó, y sostenido hasta stop_loss o target_1.

Metodología:
  1. Carga todos los contex/daily_state_*.json, extrae candidatos BUY/STRONG BUY/SELL/STRONG SELL
     (WATCH no se cuenta -- nunca fue "accionable").
  2. DEDUPLICA: mismo ticker+dirección recomendado en apariciones consecutivas (mañana/tarde del
     mismo día, o días seguidos con hueco <= SIGNAL_COOLDOWN sesiones) cuenta como UNA sola señal
     -- la primera aparición -- no una por cada re-aparición del mismo setup aún no operado.
  3. FILL: desde la fecha de la señal, busca en los siguientes FILL_WINDOW días de mercado si el
     precio toca la zona de entrada [entry_zone_low, entry_zone_high]. Si no rellena, se descarta
     (nunca se habría ejecutado realmente).
  4. HOLD: desde el fill, camina día a día hasta el límite superior de `holding_days_estimate`
     (1-3 o 5-10 sesiones, tal y como lo planteó el propio risk_manager). Si el low (long) /
     high (short) toca el stop primero -> -1R. Si el high (long) / low (short) toca target_1
     primero -> +rr_ratio_1 R. Ambos el mismo día -> se asume el peor caso (stop) por prudencia.
     Si no toca ninguno -> se cierra a mercado al precio de cierre del último día, R parcial.
  5. $ = R * max_loss_dollars -- el riesgo máximo en dólares que el propio risk_manager ya había
     calculado para el tamaño de posición propuesto, así que el resultado agregado es directamente
     interpretable como el rendimiento en dólares/euros que se habría obtenido operando cada señal
     con el sizing que el sistema mismo proponía.

Datos de precio: Alpaca vía data/market_data.py (mismo pipeline que usa el proyecto).
Uso: python backtest_pipeline_recommendations.py
"""
import argparse
import glob
import json
import re
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np
import pandas as pd

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from config import CONTEXT_DIR
from data.market_data import MarketDataFetcher

SIGNAL_COOLDOWN = 5   # sesiones de hueco para contar una reaparición como señal NUEVA (--cooldown)
FILL_WINDOW = 5        # sesiones para que el precio toque la zona de entrada
DEFAULT_MAX_HOLD = 10   # fallback si no se puede parsear holding_days_estimate
TARGET_N = 1            # 1 o 2 -- qué objetivo del risk_manager usar como salida (--target)


def parse_max_hold(s):
    m = re.findall(r"\d+", s or "")
    return int(m[-1]) if m else DEFAULT_MAX_HOLD


def load_signals():
    """Extrae candidatos accionables de todos los daily_state, deduplicados por
    (ticker, direction) con cooldown de SIGNAL_COOLDOWN sesiones."""
    raw = []
    for f in sorted(glob.glob("contex/daily_state_*.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        date = d.get("date")
        session = d.get("session", "morning")
        regime = (d.get("market") or {}).get("regime", "")
        if not date:
            continue
        for c in d.get("candidates", []):
            if c.get("recommendation") not in ("BUY", "STRONG BUY", "SELL", "STRONG SELL"):
                continue
            ta = c.get("ta_data") or {}
            rd = c.get("risk_data") or {}
            direction = ta.get("direction") or ("short" if c["recommendation"] in ("SELL", "STRONG SELL") else "long")
            entry = rd.get("entry_price")
            stop = rd.get("stop_loss")
            t1 = rd.get("target_1")
            t2 = rd.get("target_2")
            target = t2 if (TARGET_N == 2 and t2) else t1
            rr = rd.get("rr_ratio_2") if (TARGET_N == 2 and t2) else rd.get("rr_ratio_1")
            max_loss = rd.get("max_loss_dollars")
            pct = rd.get("position_size_pct")
            if not (entry and stop and target and max_loss):
                continue
            if pct is not None and pct > 100:
                # sizing roto (stop demasiado ajustado -> tamaño de posicion irreal,
                # ej. JD 13/05 con 625.9% de cartera). No era ejecutable en la practica.
                continue
            raw.append({
                "date": pd.Timestamp(date), "session": session, "regime": regime,
                "ticker": c["ticker"].upper(), "direction": direction,
                "recommendation": c["recommendation"], "composite_score": c.get("composite_score"),
                "entry_zone_low": rd.get("entry_zone_low", entry),
                "entry_zone_high": rd.get("entry_zone_high", entry),
                "entry": entry, "stop": stop, "target_1": target,
                "rr_1": rr, "max_loss": max_loss,
                "max_hold": parse_max_hold(rd.get("holding_days_estimate")),
                "tier": rd.get("tier"),
            })

    raw.sort(key=lambda r: (r["ticker"], r["direction"], r["date"], r["session"]))
    if SIGNAL_COOLDOWN <= 0:
        return raw
    dedup = []
    last_seen = {}
    for r in raw:
        key = (r["ticker"], r["direction"])
        if key in last_seen and (r["date"] - last_seen[key]).days <= SIGNAL_COOLDOWN:
            last_seen[key] = r["date"]
            continue
        last_seen[key] = r["date"]
        dedup.append(r)
    return dedup


def load_prices(tickers):
    f = MarketDataFetcher(CONTEXT_DIR)
    series = {}
    for i, t in enumerate(sorted(tickers), 1):
        try:
            df = f._fetch_ohlcv_alpaca(t, period="1000d", timeframe="day")
        except Exception as e:
            print(f"  [{i}/{len(tickers)}] {t}: ERROR {e}")
            continue
        if df is None or df.empty:
            print(f"  [{i}/{len(tickers)}] {t}: sin datos")
            continue
        series[t] = df.sort_index()
    return series


def simulate(sig, df):
    idx = df.index
    pos = idx.searchsorted(sig["date"])
    fill_i, fill_px = None, None
    lo, hi = sorted([sig["entry_zone_low"], sig["entry_zone_high"]])
    for j in range(pos, min(pos + FILL_WINDOW, len(idx))):
        bar = df.iloc[j]
        if bar["Low"] <= hi and bar["High"] >= lo:
            fill_i, fill_px = j, sig["entry"]
            break
    if fill_i is None:
        return {"status": "no_fill"}

    long = sig["direction"] == "long"
    stop, t1 = sig["stop"], sig["target_1"]
    end = min(fill_i + sig["max_hold"], len(idx) - 1)
    if end <= fill_i:
        return {"status": "no_data"}
    for j in range(fill_i + 1, end + 1):
        bar = df.iloc[j]
        hit_stop = bar["Low"] <= stop if long else bar["High"] >= stop
        hit_t1 = bar["High"] >= t1 if long else bar["Low"] <= t1
        if hit_stop:
            return {"status": "stop", "R": -1.0, "days": j - fill_i, "exit_date": idx[j]}
        if hit_t1:
            return {"status": "target", "R": sig["rr_1"] or 1.6, "days": j - fill_i, "exit_date": idx[j]}
    # cierre a mercado al final de la ventana
    close = df.iloc[end]["Close"]
    risk = abs(sig["entry"] - stop)
    R = (close - sig["entry"]) / risk if long else (sig["entry"] - close) / risk
    return {"status": "time_exit", "R": R, "days": end - fill_i, "exit_date": idx[end]}


def main():
    global SIGNAL_COOLDOWN, TARGET_N
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=int, choices=(1, 2), default=1,
                     help="usar target_1 (rr~1.6) o target_2 (rr~2.5) como salida")
    ap.add_argument("--cooldown", type=int, default=5,
                     help="dias de hueco para deduplicar reapariciones del mismo ticker+direccion; 0 = sin deduplicar")
    args = ap.parse_args()
    TARGET_N = args.target
    SIGNAL_COOLDOWN = args.cooldown

    print(f"Config: target_{TARGET_N}, cooldown={SIGNAL_COOLDOWN}d")
    print("Cargando señales accionables de daily_state_*.json...")
    signals = load_signals()
    print(f"Señales {'deduplicadas' if SIGNAL_COOLDOWN>0 else 'SIN deduplicar'} (cooldown {SIGNAL_COOLDOWN}d): {len(signals)}")

    tickers = sorted(set(s["ticker"] for s in signals))
    print(f"\nDescargando precios para {len(tickers)} tickers...")
    prices = load_prices(tickers)

    today = pd.Timestamp(datetime.now().date())
    results = []
    for sig in signals:
        df = prices.get(sig["ticker"])
        if df is None:
            results.append({**sig, "status": "no_price_data"})
            continue
        # descarta señales demasiado recientes para tener ventana de fill+hold completa
        # (en sesiones de mercado aprox., usando dias naturales *1.4 como margen)
        needed_days = int((FILL_WINDOW + sig["max_hold"]) * 1.4)
        if (today - sig["date"]).days < needed_days:
            results.append({**sig, "status": "too_recent"})
            continue
        r = simulate(sig, df)
        results.append({**sig, **r})

    resolved = [r for r in results if r["status"] in ("stop", "target", "time_exit")]
    no_fill = [r for r in results if r["status"] == "no_fill"]
    too_recent = [r for r in results if r["status"] in ("too_recent", "no_data")]
    no_data = [r for r in results if r["status"] == "no_price_data"]

    print(f"\n{'='*78}")
    print("RESULTADO -- simulación de TODAS las señales accionables del pipeline")
    print(f"{'='*78}")
    print(f"Señales totales (deduplicadas):  {len(signals)}")
    print(f"  Resueltas (fill + salida):     {len(resolved)}")
    print(f"  Nunca rellenaron (no fill):    {len(no_fill)}")
    print(f"  Demasiado recientes / sin ventana: {len(too_recent)}")
    print(f"  Sin datos de precio:           {len(no_data)}")

    if resolved:
        wins = [r for r in resolved if r["R"] > 0]
        total_R = sum(r["R"] for r in resolved)
        total_usd = sum(r["R"] * r["max_loss"] for r in resolved)
        print(f"\nDe las {len(resolved)} resueltas:")
        print(f"  Win rate: {len(wins)/len(resolved):.1%}")
        print(f"  R medio: {total_R/len(resolved):+.3f}")
        print(f"  R total acumulado: {total_R:+.2f}")
        print(f"  $/€ total (R * max_loss_dollars de cada señal): {total_usd:+.2f}")
        by_status = defaultdict(list)
        for r in resolved:
            by_status[r["status"]].append(r)
        for st, rs in by_status.items():
            print(f"    {st:10s}: n={len(rs):3d}  R medio={np.mean([x['R'] for x in rs]):+.2f}")

        print("\n-- Por banda de score --")
        for lo, hi, lab in [(7.5, 99, "STRONG (>=7.5)"), (6.0, 7.5, "BUY (6.0-7.5)"), (0, 6.0, "<6.0 (raro, no debería)")]:
            band = [r for r in resolved if r["composite_score"] is not None and lo <= r["composite_score"] < hi]
            if band:
                w = sum(1 for r in band if r["R"] > 0) / len(band)
                print(f"  {lab:26s} n={len(band):3d}  win={w:.0%}  R medio={np.mean([r['R'] for r in band]):+.3f}  $ total={sum(r['R']*r['max_loss'] for r in band):+.2f}")

        print("\n-- Por dirección --")
        for d in ("long", "short"):
            band = [r for r in resolved if r["direction"] == d]
            if band:
                w = sum(1 for r in band if r["R"] > 0) / len(band)
                print(f"  {d:6s} n={len(band):3d}  win={w:.0%}  R medio={np.mean([r['R'] for r in band]):+.3f}  $ total={sum(r['R']*r['max_loss'] for r in band):+.2f}")

        print("\n-- Por TIER (A=núcleo, B=growth alta beta, C=especulativo) [§5 estrategia_riesgo.md] --")
        for tier in ("A", "B", "C"):
            band = [r for r in resolved if r.get("tier") == tier]
            if band:
                w = sum(1 for r in band if r["R"] > 0) / len(band)
                print(f"  {tier:6s} n={len(band):3d}  win={w:.0%}  R medio={np.mean([r['R'] for r in band]):+.3f}  $ total={sum(r['R']*r['max_loss'] for r in band):+.2f}")

        print("\n-- Por RÉGIMEN de mercado en el momento de la señal --")
        regimes = defaultdict(list)
        for r in resolved:
            reg = (r.get("regime") or "").split(" —")[0].split(" -")[0].strip() or "?"
            regimes[reg].append(r)
        for reg, band in sorted(regimes.items(), key=lambda x: -len(x[1])):
            w = sum(1 for r in band if r["R"] > 0) / len(band)
            print(f"  {reg:16s} n={len(band):3d}  win={w:.0%}  R medio={np.mean([r['R'] for r in band]):+.3f}  $ total={sum(r['R']*r['max_loss'] for r in band):+.2f}")

        print("\n-- Detalle de señales resueltas --")
        for r in sorted(resolved, key=lambda x: x["date"]):
            print(f"  {r['date'].date()} {r['ticker']:6s} {r['direction']:5s} {r['recommendation']:11s} "
                  f"score={r['composite_score']:.2f}  {r['status']:10s} R={r['R']:+.2f}  "
                  f"${r['R']*r['max_loss']:+7.2f}  ({r['days']}d)")

    if no_fill:
        print(f"\n-- Nunca rellenaron ({len(no_fill)}) --")
        for r in no_fill[:20]:
            print(f"  {r['date'].date()} {r['ticker']:6s} {r['direction']:5s} score={r['composite_score']}")
        if len(no_fill) > 20:
            print(f"  ... y {len(no_fill)-20} más")


if __name__ == "__main__":
    main()
