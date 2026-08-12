"""Calendario económico — soporte de datos para R9 (docs/estrategia_riesgo.md §3).

Fuente: el feed JSON público que Forex Factory sirve para su propio widget de
calendario (`nfs.faireconomy.media`), sin necesidad de API key. Devuelve los
eventos de la semana en curso (title, country, date ISO, impact, forecast,
previous). FF limita las descargas del fichero semanal a 2 cada 5 minutos, así
que se cachea en disco — de sobra para las dos corridas diarias del pipeline.
"""
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

from config import (
    CONTEXT_DIR, ECONOMIC_CALENDAR_URL, ECONOMIC_CALENDAR_CACHE_TTL_HOURS,
    MACRO_EVENT_COUNTRIES, MACRO_EVENT_MIN_IMPACT, LOGS_DIR,
)
from utils.logger import get_logger

logger = get_logger("EconomicCalendar", LOGS_DIR)

CACHE_FILE = Path(CONTEXT_DIR) / "economic_calendar_cache.json"

_IMPACT_RANK = {"Holiday": -1, "Low": 0, "Medium": 1, "High": 2}


def fetch_calendar_events(force: bool = False) -> list:
    """Eventos crudos de la semana en curso, cacheados en disco (TTL horas)."""
    if not force and CACHE_FILE.exists():
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            age_hours = (time.time() - cached.get("fetched_at", 0)) / 3600
            if age_hours < ECONOMIC_CALENDAR_CACHE_TTL_HOURS:
                return cached.get("events", [])
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Caché de calendario económico corrupta, refetching: {e}")

    try:
        resp = requests.get(ECONOMIC_CALENDAR_URL, timeout=10)
        resp.raise_for_status()
        events = resp.json()
        if not isinstance(events, list):
            raise ValueError(f"Formato inesperado del feed: {type(events)}")
    except Exception as e:
        logger.error(f"No se pudo descargar el calendario económico: {e}")
        if CACHE_FILE.exists():
            try:
                return json.loads(CACHE_FILE.read_text(encoding="utf-8")).get("events", [])
            except (json.JSONDecodeError, OSError):
                pass
        return []

    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = CACHE_FILE.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps({"fetched_at": time.time(), "events": events}),
            encoding="utf-8",
        )
        tmp_path.replace(CACHE_FILE)
    except OSError as e:
        logger.warning(f"No se pudo escribir la caché del calendario económico: {e}")

    return events


def _normalize(ev: dict) -> dict:
    return {
        "title": ev.get("title", ""),
        "country": ev.get("country", ""),
        "date": ev.get("date", ""),
        "impact": ev.get("impact", ""),
        "forecast": ev.get("forecast", ""),
        "previous": ev.get("previous", ""),
    }


def get_high_impact_events(target: date = None, countries: list = None,
                            min_impact: str = None, now: datetime = None) -> list:
    """Eventos del feed en `target` (hoy por defecto) que TODAVÍA NO SE HAN
    PUBLICADO, filtrados por país e impacto mínimo. Un evento de `target` cuya
    hora ya pasó (p.ej. el CPI de las 8:30am ET una vez publicado el dato) se
    excluye: la incertidumbre binaria que motiva R9 (utils/risk_policy.py) deja
    de existir en cuanto el dato es conocido, aunque siga siendo "hoy". Formato
    normalizado: title/country/date/impact/forecast/previous."""
    target = target or date.today()
    countries = countries if countries is not None else MACRO_EVENT_COUNTRIES
    min_rank = _IMPACT_RANK.get(min_impact or MACRO_EVENT_MIN_IMPACT, 2)
    now = now or datetime.now(timezone.utc)

    out = []
    for ev in fetch_calendar_events():
        if countries and ev.get("country") not in countries:
            continue
        if _IMPACT_RANK.get(ev.get("impact", ""), -1) < min_rank:
            continue
        raw_date = ev.get("date", "")
        try:
            ev_dt = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
        except ValueError:
            continue
        if ev_dt.date() != target:
            continue
        if ev_dt <= now:
            continue  # ya se publicó — deja de ser riesgo pendiente para R9
        out.append(_normalize(ev))
    return out
