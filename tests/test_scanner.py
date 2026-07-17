import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.mock_data import MOCK_QUOTES, MOCK_CLAUDE_SCANNER_RESPONSE
from agents.market_scanner import MarketScanner


def test_apply_basic_filters_price():
    # avg_vol_20d_real es la puerta de liquidez (volumen consolidado real);
    # avg_vol_20d (IEX) solo alimenta vol_ratio. Sin avg_vol_20d_real el filtro
    # asume líquido (ver test_apply_basic_filters_liquidity_fallback).
    scanner = MarketScanner.__new__(MarketScanner)
    quotes = {
        "VALID": {"price": 100.0, "volume": 2_000_000, "avg_vol_20d": 1_000_000, "avg_vol_20d_real": 1_000_000, "change_pct": 1.0, "high_52w": 120.0, "low_52w": 80.0},
        "TOO_CHEAP": {"price": 0.50, "volume": 2_000_000, "avg_vol_20d": 1_000_000, "avg_vol_20d_real": 1_000_000, "change_pct": 0.0, "high_52w": 0.60, "low_52w": 0.40},
        "LOW_VOL": {"price": 50.0, "volume": 100_000, "avg_vol_20d": 100_000, "avg_vol_20d_real": 100_000, "change_pct": 0.0, "high_52w": 55.0, "low_52w": 45.0},
    }
    candidates = scanner._apply_basic_filters(quotes)
    tickers = [c.ticker for c in candidates]
    assert "VALID" in tickers
    assert "TOO_CHEAP" not in tickers
    assert "LOW_VOL" not in tickers


def test_apply_basic_filters_liquidity_fallback():
    # Sin avg_vol_20d_real (yfinance falló para este ticker): se asume líquido
    # en vez de descartarlo por un hueco de datos.
    scanner = MarketScanner.__new__(MarketScanner)
    quotes = {
        "NO_REAL_VOL": {"price": 50.0, "volume": 100_000, "avg_vol_20d": 100_000, "change_pct": 0.0, "high_52w": 55.0, "low_52w": 45.0},
    }
    candidates = scanner._apply_basic_filters(quotes)
    assert "NO_REAL_VOL" in [c.ticker for c in candidates]


def test_apply_basic_filters_real_volume_overrides_iex():
    # avg_vol_20d (IEX) alto no debe rescatar un ticker cuyo volumen real es bajo.
    scanner = MarketScanner.__new__(MarketScanner)
    quotes = {
        "IEX_INFLATED": {"price": 50.0, "volume": 600_000, "avg_vol_20d": 600_000, "avg_vol_20d_real": 200_000, "change_pct": 0.0, "high_52w": 55.0, "low_52w": 45.0},
    }
    candidates = scanner._apply_basic_filters(quotes)
    assert "IEX_INFLATED" not in [c.ticker for c in candidates]


def test_score_candidates():
    scanner = MarketScanner.__new__(MarketScanner)
    quotes = {
        "HIGH_VOL": {"price": 100.0, "volume": 3_000_000, "avg_vol_20d": 1_000_000, "change_pct": 3.0, "high_52w": 105.0, "low_52w": 70.0},
        "LOW_VOL": {"price": 100.0, "volume": 600_000, "avg_vol_20d": 1_000_000, "change_pct": 0.1, "high_52w": 150.0, "low_52w": 70.0},
    }
    candidates = scanner._apply_basic_filters(quotes)
    scored = scanner._score_candidates(candidates)
    high = next(c for c in scored if c.ticker == "HIGH_VOL")
    low = next(c for c in scored if c.ticker == "LOW_VOL")
    assert high.initial_score > low.initial_score


def test_volume_spike_signal():
    scanner = MarketScanner.__new__(MarketScanner)
    quotes = {
        "SPIKE": {"price": 100.0, "volume": 2_000_000, "avg_vol_20d": 1_000_000, "change_pct": 0.0, "high_52w": 110.0, "low_52w": 80.0},
    }
    candidates = scanner._apply_basic_filters(quotes)
    assert "volume_spike" in candidates[0].scan_signals


def test_near_52w_high_signal():
    scanner = MarketScanner.__new__(MarketScanner)
    quotes = {
        "NEAR_HIGH": {"price": 97.0, "volume": 600_000, "avg_vol_20d": 500_000, "change_pct": 0.0, "high_52w": 100.0, "low_52w": 70.0},
    }
    candidates = scanner._apply_basic_filters(quotes)
    assert "near_52w_high" in candidates[0].scan_signals


def test_mock_quotes_pass_filter():
    scanner = MarketScanner.__new__(MarketScanner)
    candidates = scanner._apply_basic_filters(MOCK_QUOTES)
    assert len(candidates) == len(MOCK_QUOTES)
