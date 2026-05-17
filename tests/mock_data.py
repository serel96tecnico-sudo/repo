import numpy as np
import pandas as pd
from datetime import datetime, timedelta


def make_ohlcv(n: int = 90, seed: int = 42, start_price: float = 100.0) -> pd.DataFrame:
    np.random.seed(seed)
    returns = np.random.randn(n) * 0.01
    close = start_price * np.exp(np.cumsum(returns))
    close = pd.Series(close)
    high = close + np.abs(np.random.randn(n) * 0.5)
    low = close - np.abs(np.random.randn(n) * 0.5)
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(np.random.randint(500_000, 3_000_000, n), dtype=float)

    dates = pd.date_range(end=datetime.today(), periods=n, freq="B")
    return pd.DataFrame(
        {"Open": open_.values, "High": high.values, "Low": low.values, "Close": close.values, "Volume": volume.values},
        index=dates,
    )


MOCK_QUOTES = {
    "AAPL": {"price": 210.5, "prev_price": 207.0, "change_pct": 1.69, "volume": 80_000_000, "avg_vol_20d": 55_000_000, "high_52w": 220.0, "low_52w": 155.0},
    "NVDA": {"price": 890.0, "prev_price": 870.0, "change_pct": 2.30, "volume": 50_000_000, "avg_vol_20d": 30_000_000, "high_52w": 950.0, "low_52w": 400.0},
    "MSFT": {"price": 420.0, "prev_price": 415.0, "change_pct": 1.20, "volume": 25_000_000, "avg_vol_20d": 20_000_000, "high_52w": 440.0, "low_52w": 310.0},
    "META": {"price": 500.0, "prev_price": 490.0, "change_pct": 2.04, "volume": 18_000_000, "avg_vol_20d": 12_000_000, "high_52w": 530.0, "low_52w": 300.0},
    "AMZN": {"price": 185.0, "prev_price": 182.0, "change_pct": 1.65, "volume": 40_000_000, "avg_vol_20d": 30_000_000, "high_52w": 200.0, "low_52w": 120.0},
}

MOCK_NEWS = {
    "NVDA": [
        {"title": "NVIDIA reports record AI chip demand, raises guidance", "source": "Reuters", "published": "2026-05-05", "url": "", "summary": ""},
        {"title": "Analysts raise NVDA price targets after earnings beat", "source": "Bloomberg", "published": "2026-05-04", "url": "", "summary": ""},
    ],
    "AAPL": [
        {"title": "Apple launches new AI features in iOS 20", "source": "TechCrunch", "published": "2026-05-05", "url": "", "summary": ""},
    ],
}

MOCK_CLAUDE_TA_RESPONSE = {
    "pattern_detected": "bull_flag",
    "signals": {"rsi": "bullish", "macd": "bullish", "ema_stack": "bullish", "volume": "bullish", "bollinger": "neutral"},
    "entry_trigger": "pullback_to_ema9",
    "ta_score": 8.0,
    "ta_summary": "Strong bullish setup with clean EMA alignment and volume confirmation.",
    "support_levels": [195.0, 188.0],
    "resistance_levels": [220.0, 235.0],
}

MOCK_CLAUDE_SENTIMENT_RESPONSE = {
    "overall_sentiment": "positive",
    "sentiment_score": 0.75,
    "catalyst_found": True,
    "catalyst_description": "Record earnings beat with raised guidance",
    "risk_flags": [],
    "reasoning": "Multiple positive catalysts with no identifiable risks.",
}

MOCK_CLAUDE_SCANNER_RESPONSE = {
    "prioritized_tickers": ["NVDA", "AAPL", "META", "MSFT", "AMZN"],
    "notes": "NVDA leads on volume and momentum. All show strong price action.",
}

MOCK_CLAUDE_RISK_RESPONSE = {
    "risk_score": 7.5,
    "concerns": [],
    "validated": True,
}

MOCK_CLAUDE_SUMMARIES = {
    "summaries": [
        "NVDA is breaking out of a bull flag on 2.8x volume following earnings. Enter on pullback to EMA9. Key risk: VIX spike could cause broad selloff.",
        "AAPL testing 52-week high resistance with strong momentum. AI catalyst provides fundamental support for the move.",
    ]
}
