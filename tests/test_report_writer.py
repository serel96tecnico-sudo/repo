"""Tests de robustez del ReportWriter: la descripción del trade SIEMPRE se imprime,
aunque la API de Claude falle o devuelva una respuesta parcial (bug 2026-06-24).
"""

import logging
import types

from agents.report_writer import ReportWriter


def _fake_candidate():
    ta = types.SimpleNamespace(
        direction="long", pattern_detected="pullback",
        entry_trigger="pullback_to_ema9", ta_summary="EMA stack alcista",
    )
    risk = types.SimpleNamespace(
        entry_price=448.78, stop_loss=404.41, target_1=519.77, target_2=559.70,
        rr_ratio_1=1.6, holding_days_estimate="5-10 trading days",
        entry_note="R5: exige fuerza sobre ~$448.78", sizing_note="R3 riesgo 1.0%",
    )
    sent = types.SimpleNamespace(
        overall_sentiment="positive", catalyst_found=True,
        catalyst_description="subida de precios de foundry y avances en CoWoS",
        risk_flags=["macro_headwind"], sentiment_score=0.65,
    )
    fund = types.SimpleNamespace(
        risk_flags=[], fundamental_score=6.8, analyst_recom=1.2,
        target_price=460.40, short_float_pct=0.6, insider_trans_pct=-3.9,
    )
    return types.SimpleNamespace(
        ticker="TSM", company_name="Taiwan Semi", composite_score=7.0,
        recommendation="WATCH", current_price=438.53,
        ta_data=ta, risk_data=risk, sentiment_data=sent, fundamental_data=fund,
    )


def _rw():
    rw = ReportWriter.__new__(ReportWriter)
    rw.logger = logging.getLogger("test")
    return rw


def _market():
    return types.SimpleNamespace(
        spy_trend="Downtrend", qqq_trend="Sideways", vix_level=19.15,
        regime="BEARISH — reducir exposición",
        btc_price=61000, btc_change_pct=-2.0, eth_price=1647, eth_change_pct=-1.1,
    )


def test_fallback_summary_is_useful_and_non_empty():
    s = _rw()._fallback_summary(_fake_candidate())
    assert s.startswith("[auto]")
    assert "pullback" in s
    assert "entrada $448.78" in s
    assert "Catalizador" in s


def test_summaries_use_fallback_when_api_fails():
    rw = _rw()
    def boom(*a, **k):
        raise RuntimeError("API overloaded")
    rw._call_claude = boom
    out = rw._generate_summaries([_fake_candidate(), _fake_candidate()], _market())
    assert len(out) == 2
    assert all(s and s.startswith("[auto]") for s in out)  # nunca vacío


def test_partial_ai_response_filled_with_fallback():
    rw = _rw()
    rw._call_claude = lambda *a, **k: "Narrativa IA del setup 1."
    out = rw._generate_summaries([_fake_candidate(), _fake_candidate()], _market())
    assert out[0] == "Narrativa IA del setup 1."   # la de la IA se respeta
    assert out[1].startswith("[auto]")             # la que falta, fallback


def test_full_ai_response_used_verbatim():
    rw = _rw()
    rw._call_claude = lambda *a, **k: "Setup 1.\n===\nSetup 2."
    out = rw._generate_summaries([_fake_candidate(), _fake_candidate()], _market())
    assert out == ["Setup 1.", "Setup 2."]
