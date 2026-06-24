"""Tests del clasificador de régimen coherente (data/market_data.classify_regime).

Cubre el bug 2026-06-24: el régimen salía 'BULLISH' (solo VIX) con el SPY en
Downtrend, y el filtro de cortos lo trataba como mercado alcista bloqueando shorts.
"""

from data.market_data import classify_regime


def test_downtrend_low_vix_is_bearish_not_bullish():
    # El bug exacto: VIX bajo + precio cayendo. NUNCA debe decir BULLISH.
    r = classify_regime("Downtrend", 19.15)
    assert r.startswith("BEARISH")


def test_uptrend_low_vix_is_bullish():
    assert classify_regime("Strong Uptrend", 14.0).startswith("BULLISH")
    assert classify_regime("Uptrend", 17.0).startswith("BULLISH")


def test_uptrend_elevated_vix_forces_neutral():
    # VIX>=20 fuerza NEUTRAL aunque el precio suba (coherente con R1)
    assert classify_regime("Uptrend", 22.0).startswith("NEUTRAL")


def test_sideways_is_neutral():
    assert classify_regime("Sideways", 16.0).startswith("NEUTRAL")


def test_panic_vix_is_bearish_regardless_of_trend():
    assert classify_regime("Strong Uptrend", 32.0).startswith("BEARISH")


def test_unknown_trend_falls_back_safely():
    # Sin datos de tendencia: NEUTRAL salvo pánico de VIX
    assert classify_regime("Unknown", 16.0).startswith("NEUTRAL")
    assert classify_regime("Unknown", 35.0).startswith("BEARISH")


def test_invariant_never_bullish_in_downtrend():
    for vix in (10, 15, 19, 25, 31):
        assert not classify_regime("Downtrend", vix).startswith("BULLISH")
