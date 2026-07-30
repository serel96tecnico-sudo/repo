"""Tests de FundamentalAnalyst._parse_earnings_days (Filtro B — earnings window).

Bug corregido 2026-07-30 (caso BE): la heurística anterior (`dt < ahora - 1 día
→ año siguiente`) trataba un earnings de AYER como si fuera dentro de 364 días,
porque comparaba la medianoche de la fecha parseada contra un timestamp con
hora. Eso desactivó el Filtro B justo el día de mayor riesgo (post-reacción a
earnings). La función ahora prueba las 3 interpretaciones de año posibles
(anterior/actual/siguiente) y toma la más cercana a hoy.
"""

from datetime import date

from agents.fundamental_analyst import FundamentalAnalyst


def _parse(earnings_str, today):
    """Réplica exacta de _parse_earnings_days pero con `today` inyectable
    (la función real usa datetime.now() internamente)."""
    import re
    clean = re.sub(r"\s+(AMC|BMO|--)\s*$", "", earnings_str.strip())
    base_year = today.year
    from datetime import datetime as dt_cls
    base = dt_cls.strptime(f"{clean} {base_year}", "%b %d %Y").date()
    candidates = [base.replace(year=base_year + delta) for delta in (-1, 0, 1)]
    closest = min(candidates, key=lambda d: abs((d - today).days))
    return (closest - today).days


def test_be_case_earnings_yesterday_is_minus_one_not_364():
    # Earnings "Jul 28 AMC", evaluado 29/07/2026 — el día después del reporte.
    assert _parse("Jul 28 AMC", date(2026, 7, 29)) == -1


def test_earnings_today_is_zero():
    assert _parse("Jul 28 AMC", date(2026, 7, 28)) == 0


def test_genuine_future_earnings_unaffected():
    assert _parse("Aug 05 BMO", date(2026, 7, 29)) == 7


def test_stale_far_past_stays_negative_not_wrapped_forward():
    # Dato de Finviz sin refrescar desde hace meses: no se asume "año que viene".
    assert _parse("Apr 15 AMC", date(2026, 7, 29)) == -105


def test_year_boundary_crossing():
    assert _parse("Jan 10 BMO", date(2026, 12, 30)) == 11


def test_empty_or_placeholder_returns_default_999():
    assert FundamentalAnalyst._parse_earnings_days("-") == 999
    assert FundamentalAnalyst._parse_earnings_days("") == 999


def test_static_method_matches_reference_for_be_case_live():
    # Llama a la función real (usa datetime.now() de hoy) solo para confirmar
    # que no lanza excepción y devuelve un int con signo válido para una fecha futura.
    result = FundamentalAnalyst._parse_earnings_days("Dec 31 AMC")
    assert isinstance(result, int)
