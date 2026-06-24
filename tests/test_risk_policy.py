"""Tests de la política de riesgo R1/R2/R3/R6 (utils/risk_policy.py + orchestrator)."""

from dataclasses import dataclass

from utils.risk_policy import (
    classify_tier, is_high_beta, high_beta_cap, risk_pct_for_regime, open_position_risk,
    size_position,
)
from config import (
    HIGH_BETA_CAP_STRONG_UP, HIGH_BETA_CAP_NEUTRAL, HIGH_BETA_CAP_RISKOFF,
    RISK_PCT_STRONG_UP, RISK_PCT_UPTREND, RISK_PCT_NEUTRAL, RISK_PCT_RISKOFF,
    DEFAULT_STOP_PCT, MIN_INVEST_PER_TRADE, MAX_INVEST_PER_TRADE,
)


@dataclass
class MC:
    spy_trend: str = "Uptrend"
    qqq_trend: str = "Uptrend"
    vix_level: float = 16.0
    regime: str = "BULLISH"


# ── Clasificación de tiers ────────────────────────────────────────────────────

def test_tier_a_core():
    assert classify_tier("AAPL") == ("A", None)
    assert classify_tier("MSFT") == ("A", None)


def test_tier_b_high_beta_growth():
    assert classify_tier("AMD") == ("B", None)
    assert classify_tier("MRVL") == ("B", None)
    assert classify_tier("NVDA") == ("B", None)


def test_tier_c_subthemes():
    assert classify_tier("HIMS") == ("C", "health_spec")
    assert classify_tier("MARA") == ("C", "mineros_cripto")
    assert classify_tier("WULF") == ("C", "mineros_cripto")
    assert classify_tier("IONQ") == ("C", "quantum")


def test_fallback_by_market_cap():
    assert classify_tier("ZZZZ", market_cap=200e9) == ("A", None)
    assert classify_tier("ZZZZ", market_cap=30e9) == ("B", None)
    assert classify_tier("ZZZZ", market_cap=2e9) == ("C", "otros")
    # Sin datos → conservador (Tier C)
    assert classify_tier("ZZZZ") == ("C", "otros")


def test_high_short_float_forces_spec():
    assert classify_tier("ZZZZ", market_cap=30e9, short_float=0.25) == ("C", "otros")


def test_is_high_beta():
    assert is_high_beta("B") and is_high_beta("C")
    assert not is_high_beta("A")


# ── R1 — cap por régimen ──────────────────────────────────────────────────────

def test_cap_strong_uptrend():
    assert high_beta_cap(MC(spy_trend="Strong Uptrend", vix_level=15, regime="BULLISH")) == HIGH_BETA_CAP_STRONG_UP


def test_cap_neutral_when_vix_elevated_overrides_uptrend():
    # VIX>=20 fuerza el cap NEUTRAL aunque el SPY siga en Strong Uptrend (hoy real)
    mc = MC(spy_trend="Strong Uptrend", qqq_trend="Sideways", vix_level=20.0, regime="NEUTRAL")
    assert high_beta_cap(mc) == HIGH_BETA_CAP_NEUTRAL


def test_cap_riskoff():
    assert high_beta_cap(MC(spy_trend="Downtrend", vix_level=28, regime="BEARISH")) == HIGH_BETA_CAP_RISKOFF


# ── R3 — perfil de riesgo dinámico (inverso al VIX) ───────────────────────────

def test_r3_risk_pct_strong_uptrend_max():
    mc = MC(spy_trend="Strong Uptrend", qqq_trend="Uptrend", vix_level=15.0, regime="BULLISH")
    assert risk_pct_for_regime(mc) == RISK_PCT_STRONG_UP


def test_r3_risk_pct_uptrend():
    assert risk_pct_for_regime(MC(spy_trend="Uptrend", vix_level=19.0, regime="BULLISH")) == RISK_PCT_UPTREND


def test_r3_risk_pct_neutral_when_vix_elevated_overrides_uptrend():
    # VIX>=20 fuerza el riesgo NEUTRAL aunque el SPY siga en Strong Uptrend
    mc = MC(spy_trend="Strong Uptrend", qqq_trend="Sideways", vix_level=20.0, regime="NEUTRAL")
    assert risk_pct_for_regime(mc) == RISK_PCT_NEUTRAL


def test_r3_risk_pct_riskoff_min():
    assert risk_pct_for_regime(MC(spy_trend="Downtrend", vix_level=28.0, regime="BEARISH")) == RISK_PCT_RISKOFF


def test_r3_risk_pct_none_defaults_uptrend():
    assert risk_pct_for_regime(None) == RISK_PCT_UPTREND


# ── R6 — riesgo abierto agregado de la cartera ────────────────────────────────

def test_r6_long_with_stop():
    pf = {"acciones": [{"ticker": "X", "cantidad": 10, "precio_actual_usd": 100.0, "stop_loss": 90.0}]}
    assert open_position_risk(pf) == 100.0  # (100-90)*10


def test_r6_short_with_stop():
    pf = {"acciones": [{"ticker": "X", "cantidad": 10, "precio_actual_usd": 100.0,
                        "stop_loss": 105.0, "direccion": "short"}]}
    assert open_position_risk(pf) == 50.0  # (105-100)*10


def test_r6_no_stop_uses_default_pct():
    pf = {"acciones": [{"ticker": "X", "cantidad": 10, "precio_actual_usd": 100.0, "stop_loss": None}]}
    assert open_position_risk(pf) == round(100.0 * DEFAULT_STOP_PCT * 10, 2)


def test_r6_profit_locked_stop_is_zero_risk():
    # Long en verde con stop por encima del precio → ganancia asegurada, riesgo 0
    pf = {"acciones": [{"ticker": "X", "cantidad": 10, "precio_actual_usd": 100.0, "stop_loss": 110.0}]}
    assert open_position_risk(pf) == 0.0


def test_r6_empty_portfolio():
    assert open_position_risk(None) == 0.0
    assert open_position_risk({}) == 0.0


# ── Banda de inversión bruta €500-800 (size_position) ─────────────────────────

CAP = 1400.0  # MAX_POSITION_PCT (0.20) × 7000

def test_band_example_400_stock_min_two_shares():
    # Ejemplo del operador: acción de €400 → 1 acc (€400) < suelo → 2 acc (€800)
    n = size_position(target_risk=140, risk_per_share=200, entry=400.0, max_position_value=CAP)
    assert n == 2
    assert n * 400.0 == 800.0


def test_band_caps_gross_at_max():
    # Riesgo pediría mucho; la banda recorta a <=800€ de inversión bruta
    n = size_position(target_risk=10_000, risk_per_share=1.0, entry=50.0, max_position_value=CAP)
    assert n == int(MAX_INVEST_PER_TRADE / 50.0)  # 16 acc = 800€
    assert n * 50.0 <= MAX_INVEST_PER_TRADE


def test_band_within_range_keeps_risk_sizing():
    # Inversión bruta cae dentro de [500,800] → respeta el sizing por riesgo
    n = size_position(target_risk=140, risk_per_share=10.0, entry=50.0, max_position_value=CAP)
    # base por riesgo = 14 acc → 700€, dentro de banda
    assert n == 14
    assert MIN_INVEST_PER_TRADE <= n * 50.0 <= MAX_INVEST_PER_TRADE


def test_band_expensive_stock_min_one_share():
    # Acción más cara que el tope de banda → mínimo 1 acción (indivisible), bajo el tope de capital
    n = size_position(target_risk=140, risk_per_share=100.0, entry=1000.0, max_position_value=CAP)
    assert n == 1


def test_band_floor_bumps_to_min_invest():
    # Acción de €120: 1 acc=120 <500 → sube hasta 5 acc=600 (>=500)
    n = size_position(target_risk=70, risk_per_share=999.0, entry=120.0, max_position_value=CAP)
    assert n * 120.0 >= MIN_INVEST_PER_TRADE
    assert (n - 1) * 120.0 < MIN_INVEST_PER_TRADE


def test_band_capital_ceiling_caps_cheap_stock():
    # Acción muy barata, riesgo enorme: la banda recorta a 800€, no al tope de capital
    n = size_position(target_risk=99_999, risk_per_share=0.5, entry=5.0, max_position_value=CAP)
    assert n == int(MAX_INVEST_PER_TRADE / 5.0)  # 160 acc = 800€


def test_band_invalid_inputs():
    assert size_position(140, 0, 50.0, CAP) == 0
    assert size_position(140, 10.0, 0, CAP) == 0
