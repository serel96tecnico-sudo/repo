import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.indicators import (
    calculate_rsi,
    calculate_macd,
    calculate_ema,
    calculate_bollinger_bands,
    calculate_atr,
    calculate_adx,
    calculate_all_indicators,
    score_technical_setup,
)


@pytest.fixture
def price_series():
    np.random.seed(42)
    prices = pd.Series(100 + np.cumsum(np.random.randn(100) * 0.5))
    return prices


@pytest.fixture
def ohlcv_df():
    np.random.seed(42)
    n = 100
    close = pd.Series(100 + np.cumsum(np.random.randn(n) * 0.5))
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(np.random.randint(500_000, 2_000_000, n))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume})


def test_rsi_range(price_series):
    rsi = calculate_rsi(price_series)
    valid = rsi.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_rsi_length(price_series):
    rsi = calculate_rsi(price_series)
    assert len(rsi) == len(price_series)


def test_macd_returns_three_series(price_series):
    macd, signal, hist = calculate_macd(price_series)
    assert len(macd) == len(price_series)
    assert len(signal) == len(price_series)
    assert len(hist) == len(price_series)


def test_macd_histogram_equals_diff(price_series):
    macd, signal, hist = calculate_macd(price_series)
    diff = macd - signal
    pd.testing.assert_series_equal(hist, diff)


def test_ema_converges(price_series):
    ema = calculate_ema(price_series, 9)
    assert not ema.iloc[-1] != ema.iloc[-1]  # not NaN


def test_bollinger_bands_upper_above_lower(price_series):
    upper, mid, lower, pct_b = calculate_bollinger_bands(price_series)
    valid_mask = upper.notna() & lower.notna()
    assert (upper[valid_mask] >= lower[valid_mask]).all()


def test_atr_positive(ohlcv_df):
    atr = calculate_atr(ohlcv_df["High"], ohlcv_df["Low"], ohlcv_df["Close"])
    valid = atr.dropna()
    assert (valid >= 0).all()


def test_adx_range(ohlcv_df):
    adx = calculate_adx(ohlcv_df["High"], ohlcv_df["Low"], ohlcv_df["Close"])
    valid = adx.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()


def test_calculate_all_indicators_keys(ohlcv_df):
    ind = calculate_all_indicators(ohlcv_df)
    required = ["price", "rsi_14", "macd", "ema9", "ema21", "ema50", "atr_14", "adx_14"]
    for key in required:
        assert key in ind, f"Missing key: {key}"


def test_score_bullish_setup():
    indicators = {
        "rsi_14": 55, "rsi_prev": 50,
        "macd": 0.5, "macd_signal": 0.3,
        "macd_histogram": 0.2, "macd_histogram_prev": 0.1,
        "price": 110, "ema9": 108, "ema21": 105, "ema50": 100,
        "volume_ratio_20d": 2.5,
        "adx_14": 35,
    }
    score = score_technical_setup(indicators)
    assert score >= 7.0, f"Bullish setup should score high, got {score}"


def test_score_bearish_setup():
    indicators = {
        "rsi_14": 75, "rsi_prev": 72,
        "macd": -0.5, "macd_signal": 0.1,
        "macd_histogram": -0.6, "macd_histogram_prev": -0.3,
        "price": 90, "ema9": 95, "ema21": 100, "ema50": 105,
        "volume_ratio_20d": 0.8,
        "adx_14": 10,
    }
    score = score_technical_setup(indicators)
    assert score < 3.0, f"Bearish setup should score low, got {score}"


def test_score_max_10(ohlcv_df):
    ind = calculate_all_indicators(ohlcv_df)
    score = score_technical_setup(ind)
    assert 0.0 <= score <= 10.0
