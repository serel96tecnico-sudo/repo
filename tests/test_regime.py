"""Tests del clasificador de régimen coherente (data/market_data.classify_regime).

Cubre el bug 2026-06-24: el régimen salía 'BULLISH' (solo VIX) con el SPY en
Downtrend, y el filtro de cortos lo trataba como mercado alcista bloqueando shorts.
"""

from data.market_data import classify_regime, classify_trend


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


def test_pullback_low_vix_is_neutral_long_biased():
    # Dip en tendencia alcista, VIX contenido: NEUTRAL (no risk-off) con sesgo a largos
    r = classify_regime("Pullback", 17.9)
    assert r.startswith("NEUTRAL")
    assert "largos" in r


def test_pullback_elevated_vix_is_generic_neutral():
    assert classify_regime("Pullback", 22.0).startswith("NEUTRAL")


# ── Clasificador de tendencia (estructura completa con EMA50) ──────────────────

def test_trend_pullback_not_downtrend():
    # Precio bajo las cortas (e9, e21) pero estructura de fondo intacta (e21 > e50,
    # precio > e50) → PULLBACK alcista, NO Downtrend. Es el bug del 2026-06-25.
    assert classify_trend(price=96, e9=97, e21=98, e50=95) == "Pullback"


def test_trend_real_downtrend_requires_broken_structure():
    # Estructura de fondo rota (e21 < e50), pero el precio aún sobre la e9 → Downtrend
    # (no Strong): exige e21 < e50, cosa que la lógica antigua ignoraba.
    assert classify_trend(price=96, e9=95, e21=97, e50=99) == "Downtrend"


def test_trend_strong_downtrend_full_stack():
    assert classify_trend(price=88, e9=90, e21=95, e50=98) == "Strong Downtrend"


def test_trend_strong_and_plain_uptrend():
    assert classify_trend(price=110, e9=108, e21=105, e50=100) == "Strong Uptrend"
    assert classify_trend(price=106, e9=104, e21=105, e50=100) == "Uptrend"


def test_trend_pullback_below_ema50_is_not_pullback():
    # Si el precio pierde la EMA50 ya no es un simple pullback (estructura cediendo)
    assert classify_trend(price=94, e9=96, e21=97, e50=95) != "Pullback"
