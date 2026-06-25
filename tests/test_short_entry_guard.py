"""Tests de la guardia R4 para cortos (simétrico a R5).

R4 entra el corto en el pullback a la EMA9. Pero si el precio se ha desplomado
muy por debajo de la EMA9 (downtrend agudo), ese rebote-entrada queda irreal y
el corto es WATCH-only: se marca `entry_extended` y se anota el motivo, sin
generar un SELL accionable con fill fantasma. Umbral: SHORT_EXTENDED_ATR_MAX ATR.
"""

import logging

from agents.risk_manager import RiskManager
from models.schemas import TAResult
from config import SHORT_EXTENDED_ATR_MAX


def _mk_short(ticker, price, ema9, ema21, atr, support=None, resistance=None):
    return TAResult(
        ticker=ticker,
        analysis_date="2026-06-25",
        indicators={"price": price, "ema9": ema9, "ema21": ema21, "atr_14": atr},
        support_levels=support or [price * 0.9, price * 0.8],
        resistance_levels=resistance or [],
        direction="short",
        ta_score=8.0,
        pattern_detected="breakdown",
        entry_trigger="rejection_at_resistance",
        ta_summary="test",
    )


def _rm():
    rm = RiskManager.__new__(RiskManager)
    rm._ask_claude_risk_score = lambda *a, **k: 7.0
    rm.logger = logging.getLogger("test_short_guard")
    return rm


def test_short_overextended_flagged_watch():
    # RKLB 2026-06-25: precio 80.85, EMA9 97.23, ATR ~9.76 → rebote-entrada ~1.7 ATR
    ta = _mk_short("RKLB", price=80.85, ema9=97.2262, ema21=105.59, atr=9.76)
    r = _rm()._calculate_risk_params(ta, tier="C")
    assert r is not None
    assert r.entry_extended is True
    assert "sobre-extendido" in r.entry_note.lower()


def test_short_modest_pullback_actionable():
    # NOW 2026-06-25: precio 90.09, EMA9 96.88, ATR ~6.09 → rebote-entrada ~1.1 ATR
    ta = _mk_short("NOW", price=90.09, ema9=96.8843, ema21=101.06, atr=6.09)
    r = _rm()._calculate_risk_params(ta, tier="A")
    assert r is not None
    assert r.entry_extended is False
    assert r.entry_note == ""


def test_short_threshold_boundary():
    # Justo por encima del umbral debe marcarse; justo por debajo no.
    atr = 2.0
    over = (SHORT_EXTENDED_ATR_MAX + 0.3) * atr  # entrada quedará > umbral sobre precio
    # entry = ema9*0.998 ; queremos (entry - price)/atr > umbral
    price = 100.0
    ema9 = price + over / 0.998
    ta = _mk_short("OVER", price=price, ema9=round(ema9, 2), ema21=ema9 + 2, atr=atr)
    r = _rm()._calculate_risk_params(ta, tier="B")
    assert r is not None and r.entry_extended is True

    under = (SHORT_EXTENDED_ATR_MAX - 0.3) * atr
    ema9b = price + under / 0.998
    ta2 = _mk_short("UNDER", price=price, ema9=round(ema9b, 2), ema21=ema9b + 2, atr=atr)
    r2 = _rm()._calculate_risk_params(ta2, tier="B")
    assert r2 is not None and r2.entry_extended is False


def test_short_evening_market_entry_not_flagged():
    # Sesión de tarde: entrada a mercado, la guardia no aplica
    ta = _mk_short("RKLB", price=80.85, ema9=97.2262, ema21=105.59, atr=9.76)
    r = _rm()._calculate_risk_params(ta, tier="C", session="evening")
    assert r is not None
    assert r.entry_extended is False
