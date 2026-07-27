"""Puerta de calidad y protección del núcleo de la watchlist (2026-07-27).

Regresión del incidente A-F: la rotación one-in-one-out persistía descubrimientos de
momentum y expulsaba el núcleo curado, degenerando la watchlist a las primeras letras.
Ahora solo promociona calidad (long_screener con fundamental alto) y el núcleo
(source: manual) es intocable.
"""

import json
import logging
import types

import agents.fundamental_analyst as fa
from agents.fundamental_analyst import FundamentalAnalyst
from config import WATCHLIST_MAX_SIZE, WATCHLIST_PROMOTE_MIN_FUND


def _fa():
    obj = FundamentalAnalyst.__new__(FundamentalAnalyst)
    obj.logger = logging.getLogger("test")
    return obj


def _cand(ticker, source="long_screener"):
    return types.SimpleNamespace(ticker=ticker, scan_signals=[source])


# ── _update_watchlist: núcleo protegido + cap ─────────────────────────────────

def test_manual_core_never_evicted_only_auto_recycled(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "CONTEXT_DIR", tmp_path)
    # Lleno hasta el cap: (MAX-5) manuales + 5 auto MÁS antiguos que los manuales
    manual = [{"ticker": f"M{i}", "added": "2020-01-01", "source": "manual"}
              for i in range(WATCHLIST_MAX_SIZE - 5)]
    auto = [{"ticker": f"A{i}", "added": "2019-01-01", "source": "long_screener"}
            for i in range(5)]
    entries = manual + auto
    data = {"entries": entries, "tickers": [e["ticker"] for e in entries], "updated": "2020-01-01"}
    (tmp_path / "watchlist.json").write_text(json.dumps(data), encoding="utf-8")

    # Añado 5 nombres de calidad → total = MAX+5 → hay que reciclar 5
    _fa()._update_watchlist([_cand(f"NEW{i}") for i in range(5)])

    out = json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8"))
    tickers = set(out["tickers"])

    # el núcleo manual sobrevive ENTERO aunque fuera "más nuevo" que los auto
    assert all(f"M{i}" in tickers for i in range(WATCHLIST_MAX_SIZE - 5))
    # los nuevos de calidad entran
    assert all(f"NEW{i}" in tickers for i in range(5))
    # los 5 auto antiguos son los reciclados
    assert all(f"A{i}" not in tickers for i in range(5))
    assert len(tickers) == WATCHLIST_MAX_SIZE


def test_under_cap_grows_without_eviction(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "CONTEXT_DIR", tmp_path)
    data = {"entries": [{"ticker": "AAA", "added": "2020-01-01", "source": "manual"}],
            "tickers": ["AAA"], "updated": "2020-01-01"}
    (tmp_path / "watchlist.json").write_text(json.dumps(data), encoding="utf-8")

    _fa()._update_watchlist([_cand("BBB")])

    out = json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8"))
    assert set(out["tickers"]) == {"AAA", "BBB"}   # crece, no expulsa


def test_all_manual_over_cap_never_touches_core(tmp_path, monkeypatch):
    monkeypatch.setattr(fa, "CONTEXT_DIR", tmp_path)
    # Núcleo manual ya por encima del cap: una promoción no debe expulsar a nadie manual
    manual = [{"ticker": f"M{i}", "added": "2020-01-01", "source": "manual"}
              for i in range(WATCHLIST_MAX_SIZE + 2)]
    data = {"entries": manual, "tickers": [e["ticker"] for e in manual], "updated": "2020-01-01"}
    (tmp_path / "watchlist.json").write_text(json.dumps(data), encoding="utf-8")

    _fa()._update_watchlist([_cand("NEWQ")])

    out = json.loads((tmp_path / "watchlist.json").read_text(encoding="utf-8"))
    tickers = set(out["tickers"])
    assert all(f"M{i}" in tickers for i in range(WATCHLIST_MAX_SIZE + 2))  # núcleo intacto
    assert "NEWQ" in tickers                                              # se añade igual


# ── _run_screener: solo promociona calidad ────────────────────────────────────

def _wire_screener(obj, monkeypatch, fund_scores):
    """Cablea un _run_screener determinista: cada screener (en orden long, short,
    ta_weekly, ta_monthly) devuelve un ticker, con el fundamental_score indicado."""
    monkeypatch.setattr(fa.time, "sleep", lambda *a, **k: None)
    seq = iter([["QUAL"], ["SHRT"], ["MOMW"], ["MOMM"]])
    monkeypatch.setattr(obj, "_scrape_finviz_screener", lambda *a, **k: next(seq))
    monkeypatch.setattr(obj, "_fetch_finviz",
                        lambda t: ({"Price": "10", "Company": t, "Sector": "Tech", "Change": "1"}, False))
    monkeypatch.setattr(obj, "_build_result",
                        lambda t, d, p: types.SimpleNamespace(blocked=False,
                                                              fundamental_score=fund_scores[t]))
    promoted = []
    monkeypatch.setattr(obj, "_update_watchlist",
                        lambda cands: promoted.extend(c.ticker for c in cands))
    return promoted


def test_only_long_screener_promotes_momentum_stays_ephemeral(monkeypatch):
    obj = _fa()
    # Todos con fundamental alto: aun así, momentum/short NO promocionan (no son calidad)
    promoted = _wire_screener(obj, monkeypatch, {"QUAL": 9.0, "SHRT": 9.0, "MOMW": 9.0, "MOMM": 9.0})
    new = obj._run_screener(set(), {})
    assert {c.ticker for c in new} == {"QUAL", "SHRT", "MOMW", "MOMM"}  # todos analizados hoy
    assert promoted == ["QUAL"]                                        # solo el de calidad persiste


def test_low_fundamental_long_screener_does_not_promote(monkeypatch):
    obj = _fa()
    low = WATCHLIST_PROMOTE_MIN_FUND - 0.5
    promoted = _wire_screener(obj, monkeypatch, {"QUAL": low, "SHRT": 9.0, "MOMW": 9.0, "MOMM": 9.0})
    obj._run_screener(set(), {})
    assert promoted == []   # long_screener pero por debajo del suelo de calidad → no entra
