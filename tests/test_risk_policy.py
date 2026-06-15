"""Tests de la política de riesgo R1/R2/R3 (utils/risk_policy.py + orchestrator)."""

from dataclasses import dataclass

from utils.risk_policy import (
    classify_tier, is_high_beta, high_beta_cap, is_neutral_high_vol,
)
from config import (
    HIGH_BETA_CAP_STRONG_UP, HIGH_BETA_CAP_NEUTRAL, HIGH_BETA_CAP_RISKOFF,
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


# ── R3 — condición de half-size ───────────────────────────────────────────────

def test_r3_active_today():
    mc = MC(spy_trend="Strong Uptrend", qqq_trend="Sideways", vix_level=20.0, regime="NEUTRAL")
    assert is_neutral_high_vol(mc) is True


def test_r3_inactive_low_vix():
    mc = MC(spy_trend="Sideways", qqq_trend="Sideways", vix_level=15.0, regime="NEUTRAL")
    assert is_neutral_high_vol(mc) is False


def test_r3_inactive_bullish():
    mc = MC(spy_trend="Strong Uptrend", qqq_trend="Uptrend", vix_level=22.0, regime="BULLISH")
    assert is_neutral_high_vol(mc) is False
