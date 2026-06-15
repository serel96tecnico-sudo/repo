"""Tests del paso 2 — colocación de entrada "pullback por tier" (risk_manager).

Verifica que un largo extendido no se compra a mercado (chase), sino en pullback
(Tier C) o en el retest con confirmación (Tier B); y que los casos no extendidos
o cerca de soporte siguen entrando a mercado/soporte.
"""

from agents.risk_manager import RiskManager
from models.schemas import TAResult


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
    # Evitar la llamada a Claude para el risk_score
    rm._ask_claude_risk_score = lambda *a, **k: 7.0
    import logging
    rm.logger = logging.getLogger("test_entry")
    return rm


def test_extended_tier_c_enters_on_pullback_not_market():
    # price 1.8 ATR sobre EMA9 → extendido. Sin soporte cercano por debajo.
    ta = _mk_ta("HIMS", price=28.80, ema9=28.0, ema21=27.6, atr=0.45)
    r = _rm()._calculate_risk_params(ta, tier="C", subtheme="health_spec")
    assert r is not None
    # No se compra a mercado: la entrada queda por debajo del precio actual
    assert r.entry_price < 28.80
    assert "Pullback por tier (C)" in r.entry_note
    # Abrir en verde/plano: el precio de mercado queda por ENCIMA de la entrada
    assert 28.80 >= r.entry_zone_high


def test_extended_tier_b_requires_confirmation_retest():
    ta = _mk_ta("MRVL", price=292.0, ema9=285.0, ema21=283.0, atr=4.5)
    r = _rm()._calculate_risk_params(ta, tier="B")
    assert r is not None
    assert r.entry_price < 292.0           # retest, no a mitad del impulso
    assert r.entry_price >= 285.0          # no por debajo de la EMA9
    assert "confirmar cierre" in r.entry_note.lower()


def test_not_extended_enters_at_market():
    # price a 0.4 ATR de la EMA9 → no extendido
    ta = _mk_ta("AAPL", price=200.0, ema9=199.0, ema21=197.0, atr=2.5)
    r = _rm()._calculate_risk_params(ta, tier="B")
    assert r is not None
    assert r.entry_price == 200.0
    assert r.entry_note == ""


def test_near_support_enters_on_support():
    # Soporte a ~1% por debajo → entrada límite sobre soporte (buena ubicación)
    ta = _mk_ta("MSFT", price=500.0, ema9=505.0, ema21=507.0, atr=8.0, support=[497.0])
    r = _rm()._calculate_risk_params(ta, tier="A")
    assert r is not None
    assert abs(r.entry_price - 497.0 * 1.002) < 0.05
    assert r.entry_note == ""


def test_extended_long_improves_rr_vs_chase():
    # El beneficio real del pullback: entrar más abajo contra un objetivo de
    # resistencia fijo mejora el R:R frente a perseguir a mercado.
    price, atr = 28.80, 0.45
    resistance0 = price * 1.1  # objetivo fijo
    ta = _mk_ta("HIMS", price=price, ema9=28.0, ema21=27.6, atr=atr,
                resistance=[resistance0, price * 1.2])
    r = _rm()._calculate_risk_params(ta, tier="C", subtheme="health_spec")
    chase_rr = (resistance0 - price) / (atr * 2.5)   # R:R comprando el spike
    assert r.entry_price <= price                     # abre en verde/plano
    assert r.rr_ratio_1 > chase_rr                    # mejor R:R que perseguir
