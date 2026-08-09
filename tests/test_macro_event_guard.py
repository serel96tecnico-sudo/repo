"""Test de integración de R9 (evento macro de alto impacto) en el orchestrator."""

import logging

import agents.orchestrator as orch
from agents.orchestrator import TradingOrchestrator
from models.schemas import FinalCandidate

HIGH_IMPACT_EVENT = [{"title": "FOMC Statement", "country": "USD",
                       "date": "2026-08-04T18:00:00-04:00", "impact": "High"}]


def _orch():
    o = TradingOrchestrator.__new__(TradingOrchestrator)
    o.logger = logging.getLogger("test_macro_guard")
    return o


def _fc(ticker, rec):
    return FinalCandidate(rank=0, ticker=ticker, company_name=ticker,
                           composite_score=8.0, recommendation=rec)


def test_demotes_buys_and_sells_on_high_impact_day(monkeypatch):
    monkeypatch.setattr(orch, "get_high_impact_events", lambda: HIGH_IMPACT_EVENT)
    final = [_fc("AAPL", "STRONG BUY"), _fc("TSLA", "SELL"), _fc("MSFT", "WATCH")]

    out = _orch()._apply_macro_event_guard(final)

    assert out[0].recommendation == "WATCH"
    assert "R9" in out[0].demotion_reason
    assert out[1].recommendation == "WATCH"
    assert "R9" in out[1].demotion_reason
    assert out[2].demotion_reason == ""  # ya era WATCH, no se toca


def test_no_change_on_quiet_day(monkeypatch):
    monkeypatch.setattr(orch, "get_high_impact_events", lambda: [])
    final = [_fc("AAPL", "STRONG BUY")]

    out = _orch()._apply_macro_event_guard(final)

    assert out[0].recommendation == "STRONG BUY"
    assert out[0].demotion_reason == ""


def test_fail_open_when_calendar_fetch_errors(monkeypatch):
    def boom():
        raise ConnectionError("feed down")

    monkeypatch.setattr(orch, "get_high_impact_events", boom)
    final = [_fc("AAPL", "STRONG BUY")]

    out = _orch()._apply_macro_event_guard(final)

    assert out[0].recommendation == "STRONG BUY"  # no bloquea el pipeline
