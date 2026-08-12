"""Tests del calendario económico (R9, docs/estrategia_riesgo.md §3)."""

from datetime import date, datetime, timedelta, timezone

import data.economic_calendar as ec
from utils.risk_policy import macro_event_guard

RAW_EVENTS = [
    {"title": "FOMC Statement", "country": "USD", "date": "2026-08-04T18:00:00-04:00",
     "impact": "High", "forecast": "", "previous": ""},
    {"title": "Fed Chair Speaks", "country": "USD", "date": "2026-08-04T18:30:00-04:00",
     "impact": "Medium", "forecast": "", "previous": ""},
    {"title": "German Factory Orders", "country": "EUR", "date": "2026-08-04T02:00:00-04:00",
     "impact": "High", "forecast": "", "previous": ""},
    {"title": "Building Permits", "country": "USD", "date": "2026-08-05T08:30:00-04:00",
     "impact": "High", "forecast": "", "previous": ""},
]

# "now" fijado antes de todos los eventos de RAW_EVENTS, para que los tests que no
# ejercitan explícitamente el filtro de hora sigan viendo los eventos como pendientes.
BEFORE_ALL_EVENTS = datetime(2026, 8, 4, 0, 0, 0, tzinfo=timezone.utc)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _patch_fetch(monkeypatch, tmp_path, payload=RAW_EVENTS, calls=None):
    monkeypatch.setattr(ec, "CACHE_FILE", tmp_path / "economic_calendar_cache.json")

    def fake_get(url, timeout=10):
        if calls is not None:
            calls.append(url)
        return _FakeResp(payload)

    monkeypatch.setattr(ec.requests, "get", fake_get)


# ── fetch_calendar_events: descarga + caché ───────────────────────────────────

def test_fetch_downloads_and_caches(monkeypatch, tmp_path):
    calls = []
    _patch_fetch(monkeypatch, tmp_path, calls=calls)

    events = ec.fetch_calendar_events()
    assert events == RAW_EVENTS
    assert len(calls) == 1
    assert ec.CACHE_FILE.exists()

    # segunda llamada dentro del TTL → usa la caché, no repite la descarga
    events2 = ec.fetch_calendar_events()
    assert events2 == RAW_EVENTS
    assert len(calls) == 1


def test_fetch_falls_back_to_cache_on_error(monkeypatch, tmp_path):
    _patch_fetch(monkeypatch, tmp_path)
    ec.fetch_calendar_events()  # llena la caché

    def broken_get(url, timeout=10):
        raise ConnectionError("boom")

    monkeypatch.setattr(ec.requests, "get", broken_get)
    events = ec.fetch_calendar_events(force=True)
    assert events == RAW_EVENTS  # cayó a la caché existente


def test_fetch_returns_empty_without_cache_on_error(monkeypatch, tmp_path):
    monkeypatch.setattr(ec, "CACHE_FILE", tmp_path / "no_cache.json")

    def broken_get(url, timeout=10):
        raise ConnectionError("boom")

    monkeypatch.setattr(ec.requests, "get", broken_get)
    assert ec.fetch_calendar_events() == []


# ── get_high_impact_events: filtros de fecha/país/impacto ────────────────────

def test_filters_by_date_country_and_impact(monkeypatch, tmp_path):
    _patch_fetch(monkeypatch, tmp_path)

    events = ec.get_high_impact_events(target=date(2026, 8, 4), now=BEFORE_ALL_EVENTS)
    titles = {e["title"] for e in events}
    # Solo USD + High + 2026-08-04: FOMC sí, Fed Chair (Medium) no,
    # German Factory Orders (EUR) no, Building Permits (05/08) no.
    assert titles == {"FOMC Statement"}


def test_no_events_on_quiet_day(monkeypatch, tmp_path):
    _patch_fetch(monkeypatch, tmp_path)
    assert ec.get_high_impact_events(target=date(2026, 8, 6), now=BEFORE_ALL_EVENTS) == []


def test_custom_countries_and_min_impact(monkeypatch, tmp_path):
    _patch_fetch(monkeypatch, tmp_path)

    events = ec.get_high_impact_events(
        target=date(2026, 8, 4), countries=["EUR"], min_impact="High",
        now=BEFORE_ALL_EVENTS,
    )
    assert {e["title"] for e in events} == {"German Factory Orders"}


# ── get_high_impact_events: filtro por hora (evento ya publicado) ────────────

_ET_OFFSET = timezone(-timedelta(hours=4))  # coincide con el offset del feed en RAW_EVENTS


def test_excludes_event_already_published_today(monkeypatch, tmp_path):
    _patch_fetch(monkeypatch, tmp_path)

    # FOMC Statement es a las 18:00-04:00; comprobamos 1 minuto después.
    after_event = datetime(2026, 8, 4, 18, 1, 0, tzinfo=_ET_OFFSET)
    events = ec.get_high_impact_events(target=date(2026, 8, 4), now=after_event)
    assert events == []


def test_still_includes_event_not_yet_published_today(monkeypatch, tmp_path):
    _patch_fetch(monkeypatch, tmp_path)

    # 1 minuto antes de las 18:00-04:00 del FOMC Statement: sigue pendiente.
    before_event = datetime(2026, 8, 4, 17, 59, 0, tzinfo=_ET_OFFSET)
    events = ec.get_high_impact_events(target=date(2026, 8, 4), now=before_event)
    assert {e["title"] for e in events} == {"FOMC Statement"}


# ── macro_event_guard (R9) ────────────────────────────────────────────────────

def test_macro_event_guard_true_when_events_present():
    assert macro_event_guard([{"title": "FOMC Statement"}]) is True


def test_macro_event_guard_false_when_no_events():
    assert macro_event_guard([]) is False
