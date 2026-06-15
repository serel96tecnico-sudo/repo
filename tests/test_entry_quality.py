"""Tests de R5 — calidad de entrada "exigir fuerza, no comprar debilidad".

Calibrado con backtest_entry_quality.py: lo que abre en rojo y salta por stop es
la DEBILIDAD (precio flojo/por debajo de la EMA9), no la extensión. Por eso un
largo débil (< 0.5 ATR sobre la EMA9) no entra a mercado, sino con una entrada-
stop en el umbral de fuerza; las entradas con fuerza (incluidos breakouts
extendidos, incl. Tier C) sí entran a mercado.
"""

from agents.risk_manager import RiskManager
from models.schemas import TAResult
from config import WEAK_ENTRY_ATR_MIN


def _mk_ta(ticker, price, ema9, ema21, atr, support=None, resistance=None):
    return TAResult(
        ticker=ticker,
        analysis_date="2026-06-15",
        indicators={"price": price, "ema9": ema9, "ema21": ema21, "atr_14": atr},
        support_levels=support or [],
        resistance_levels=resistance or [price * 1.1, price * 1.2],
        direction="long",
        ta_score=7.0,
        pattern_detected="breakout",
        entry_trigger="breakout",
        ta_summary="test",
    )


def _rm():
    rm = RiskManager.__new__(RiskManager)
    rm._ask_claude_risk_score = lambda *a, **k: 7.0
    import logging
    rm.logger = logging.getLogger("test_entry")
    return rm


def test_weak_below_ema9_requires_reclaim():
    # Precio por debajo de la EMA9 (fuerza negativa) → cuchillo cayendo
    ta = _mk_ta("WEAK", price=28.0, ema9=28.2, ema21=28.0, atr=0.5)
    r = _rm()._calculate_risk_params(ta, tier="C")
    assert r is not None
    # No se compra a mercado: la entrada-stop queda por ENCIMA del precio débil
    assert r.entry_price > 28.0
    assert "no comprar" in r.entry_note.lower()


def test_weak_barely_above_ema9_still_requires_strength():
    # 0.3 ATR sobre EMA9 → dentro del cubo malo (0-0.5), debe exigir fuerza
    atr = 2.0
    ta = _mk_ta("BARELY", price=200.6, ema9=200.0, ema21=198.0, atr=atr)
    r = _rm()._calculate_risk_params(ta, tier="B")
    assert r is not None
    expected_reclaim = round(200.0 + WEAK_ENTRY_ATR_MIN * atr, 2)
    assert abs(r.entry_price - expected_reclaim) < 0.05
    assert r.entry_price > 200.6
    assert r.entry_note != ""


def test_strong_enters_at_market():
    # 1.0 ATR sobre EMA9 → con fuerza, entrada a mercado, sin nota
    ta = _mk_ta("STRONG", price=202.0, ema9=200.0, ema21=198.0, atr=2.0)
    r = _rm()._calculate_risk_params(ta, tier="B")
    assert r is not None
    assert r.entry_price == 202.0
    assert r.entry_note == ""


def test_extended_tier_c_enters_at_market_not_pullback():
    # Backtest: breakout extendido de Tier C fue el mejor cubo → NO bloquear, entra a mercado
    ta = _mk_ta("HIMS", price=28.80, ema9=28.0, ema21=27.6, atr=0.45)
    r = _rm()._calculate_risk_params(ta, tier="C", subtheme="health_spec")
    assert r is not None
    assert r.entry_price == 28.80          # entra a mercado (fuerza ~1.8 ATR)
    assert r.entry_note == ""


def test_strong_with_near_support_enters_on_support():
    # Con fuerza y soporte a ~1% por debajo → entrada límite sobre soporte
    ta = _mk_ta("SUP", price=100.0, ema9=98.5, ema21=97.0, atr=1.0, support=[99.0])
    r = _rm()._calculate_risk_params(ta, tier="A")
    assert r is not None
    assert abs(r.entry_price - 99.0 * 1.002) < 0.05
    assert r.entry_note == ""
