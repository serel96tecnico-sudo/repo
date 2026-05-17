"""
End-to-end pipeline test with all external calls mocked.
Run: python -m pytest tests/test_e2e.py -v
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.mock_data import (
    make_ohlcv,
    MOCK_QUOTES,
    MOCK_CLAUDE_TA_RESPONSE,
    MOCK_CLAUDE_SENTIMENT_RESPONSE,
    MOCK_CLAUDE_SCANNER_RESPONSE,
    MOCK_CLAUDE_RISK_RESPONSE,
    MOCK_CLAUDE_SUMMARIES,
    MOCK_NEWS,
)


def make_mock_client():
    client = MagicMock()
    call_count = [0]

    def mock_create(**kwargs):
        call_count[0] += 1
        user_content = kwargs.get("messages", [{}])[0].get("content", "")

        resp = MagicMock()
        resp.content = [MagicMock()]

        if "prioritize" in user_content.lower() or "prioritized_tickers" in str(kwargs):
            resp.content[0].text = json.dumps(MOCK_CLAUDE_SCANNER_RESPONSE)
        elif "entry_trigger" in str(kwargs.get("system", "")):
            resp.content[0].text = json.dumps(MOCK_CLAUDE_TA_RESPONSE)
        elif "sentiment_score" in str(kwargs.get("system", "")):
            resp.content[0].text = json.dumps(MOCK_CLAUDE_SENTIMENT_RESPONSE)
        elif "risk_score" in str(kwargs.get("system", "")):
            resp.content[0].text = json.dumps(MOCK_CLAUDE_RISK_RESPONSE)
        elif "summaries" in str(kwargs.get("system", "")):
            resp.content[0].text = json.dumps(MOCK_CLAUDE_SUMMARIES)
        else:
            resp.content[0].text = json.dumps({"result": "ok", "prioritized_tickers": list(MOCK_QUOTES.keys()), "summaries": ["Test summary."]})

        return resp

    client.messages.create.side_effect = mock_create
    return client


def run_dry_run():
    """Full pipeline with real Claude API but mock market data."""
    from agents.orchestrator import TradingOrchestrator

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        with patch("data.market_data.yf") as mock_yf, \
             patch("config.CONTEXT_DIR", tmp / "contex"), \
             patch("config.OUTPUT_DIR", tmp / "output"):

            (tmp / "contex").mkdir()
            (tmp / "output").mkdir()

            mock_df = make_ohlcv(90)
            mock_yf.Ticker.return_value.history.return_value = mock_df
            mock_yf.download.return_value = _make_batch_df(list(MOCK_QUOTES.keys()))

            orch = TradingOrchestrator(override_tickers=list(MOCK_QUOTES.keys())[:3])
            report = orch.run_daily_pipeline()
            if report:
                print(f"Dry run complete. Report: {report.report_txt_path}")
            else:
                print("No report generated.")


def _make_batch_df(tickers):
    import pandas as pd
    import numpy as np
    from datetime import datetime

    dates = pd.date_range(end=datetime.today(), periods=30, freq="B")
    np.random.seed(42)

    dfs = {}
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        data = {}
        for t in tickers:
            if col == "Volume":
                data[t] = np.random.randint(500_000, 3_000_000, 30).astype(float)
            else:
                base = 100 + abs(hash(t)) % 800
                data[t] = base + np.cumsum(np.random.randn(30) * 0.5)
        dfs[col] = pd.DataFrame(data, index=dates)

    return pd.concat(dfs, axis=1)


@pytest.fixture
def tmp_dirs(tmp_path):
    (tmp_path / "contex").mkdir()
    (tmp_path / "output").mkdir()
    return tmp_path


def test_indicator_pipeline(tmp_dirs):
    from data.indicators import calculate_all_indicators, score_technical_setup
    df = make_ohlcv(90)
    ind = calculate_all_indicators(df)
    score = score_technical_setup(ind)
    assert 0 <= score <= 10
    assert ind["price"] is not None


def test_context_manager_roundtrip(tmp_dirs):
    from utils.context_manager import ContextManager
    ctx = ContextManager(tmp_dirs / "contex", tmp_dirs / "output")
    state = {"date": "2026-05-05", "candidates": [{"ticker": "AAPL"}]}
    ctx.save_daily_state(state, "2026-05-05")
    loaded = ctx.load_daily_state("2026-05-05")
    assert loaded["date"] == "2026-05-05"
    assert loaded["candidates"][0]["ticker"] == "AAPL"


def test_scanner_with_mock_client(tmp_dirs):
    from agents.market_scanner import MarketScanner
    from data.market_data import MarketDataFetcher
    import pandas as pd

    client = make_mock_client()

    with patch("data.market_data.yf") as mock_yf:
        mock_df = make_ohlcv(30)
        mock_yf.download.return_value = _make_batch_df(list(MOCK_QUOTES.keys()))
        mock_yf.Ticker.return_value.history.return_value = mock_df

        fetcher = MarketDataFetcher(tmp_dirs / "contex")

        with patch.object(fetcher, "get_universe", return_value=list(MOCK_QUOTES.keys())), \
             patch.object(fetcher, "fetch_batch_quotes", return_value=MOCK_QUOTES), \
             patch.object(fetcher, "get_market_overview") as mock_market:

            from models.schemas import MarketConditions
            mock_market.return_value = MarketConditions(
                date="2026-05-05", spy_trend="Uptrend", qqq_trend="Uptrend",
                vix_level=18.5, regime="BULLISH", spy_price=550.0, qqq_price=480.0
            )

            scanner = MarketScanner(client, fetcher)
            result = scanner.run()

            assert result is not None
            assert len(result.candidates) > 0
            assert result.total_screened == len(MOCK_QUOTES)


def test_report_files_created(tmp_dirs):
    from utils.context_manager import ContextManager
    ctx = ContextManager(tmp_dirs / "contex", tmp_dirs / "output")

    state = {
        "date": "2026-05-05",
        "candidates": [{"ticker": "NVDA", "composite_score": 8.5}],
    }
    path = ctx.save_daily_state(state)
    assert path.exists()

    json_content = json.loads(path.read_text())
    assert json_content["date"] == "2026-05-05"
