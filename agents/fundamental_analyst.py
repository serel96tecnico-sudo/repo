import json
import os
import re
import time
import requests
from datetime import datetime
from pathlib import Path

from agents.base_agent import BaseAgent
from models.schemas import ScanCandidate, FundamentalResult
from config import (
    EARNINGS_BLOCK_DAYS, EARNINGS_HOLD_BLOCK_DAYS, FUNDAMENTAL_TOP_N, CONTEXT_DIR,
    WATCHLIST_PROMOTE_MIN_FUND, WATCHLIST_MAX_SIZE, SCREENER_MIN_BETA,
)

CACHE_TTL_DAYS          = 7   # días antes de refrescar datos fundamentales
EARNINGS_REFRESH_DAYS   = 14  # siempre refresca si earnings en menos de X días

# Screener filter keys that finvizfinance accepts
# "Beta": SCREENER_MIN_BETA en los 4 screeners de descubrimiento — filtra nombres
# que apenas se mueven (EWS 0.53, BANC 0.74). NO va en los gappers (event-driven:
# un valor de beta baja gapeando +5% por noticia sigue siendo un candidato válido).
LONG_SCREENER_FILTERS = {
    "Average Volume": "Over 500K",
    "Country": "USA",
    "Analyst Recom.": "Strong Buy (1)",
    "InsiderTransactions": "Positive (>0%)",
    "Price": "Over $5",
    "Beta": SCREENER_MIN_BETA,
}
SHORT_SCREENER_FILTERS = {
    "Average Volume": "Over 500K",
    "Country": "USA",
    "Float Short": "Over 10%",
    "Performance": "Week Down",
    "Price": "Over $5",
    "Beta": SCREENER_MIN_BETA,
}
# TA screeners — cribado en timeframe semanal/mensual para descubrir nuevos candidatos
TA_WEEKLY_LONG_FILTERS = {
    "Average Volume": "Over 500K",
    "Country": "USA",
    "Price": "Over $5",
    "20-Day Simple Moving Average": "Price above SMA20",
    "50-Day Simple Moving Average": "Price above SMA50",
    "200-Day Simple Moving Average": "Price above SMA200",
    "Performance": "Week Up",
    "Performance 2": "Month Up",
    "RSI (14)": "Not Overbought (<60)",
    "Beta": SCREENER_MIN_BETA,
}
TA_MONTHLY_BREAKOUT_FILTERS = {
    "Average Volume": "Over 500K",
    "Country": "USA",
    "Price": "Over $5",
    "52-Week High/Low": "0-10% below High",
    "200-Day Simple Moving Average": "Price above SMA200",
    "Performance": "Quarter Up",
    "Performance 2": "Half Up",
    "Relative Volume": "Over 1",
    "Beta": SCREENER_MIN_BETA,
}
# Gappers — descubrimiento DIARIO de valores que se disparan HOY por noticia/evento.
# A diferencia de los screeners de arriba: corre en cada sesión (no cada 7 días),
# NO excluye sobrecompra (un gapper es sobrecomprado por definición) y NO depende
# de que el ticker esté en la watchlist. Son candidatos efímeros: se analizan hoy
# pero no se persisten en watchlist.json.
GAPPERS_ENABLED = True
GAPPERS_LIMIT   = 10   # por dirección
GAPPERS_LONG_FILTERS = {
    "Average Volume": "Over 500K",
    "Country": "USA",
    "Industry": "Stocks only (ex-Funds)",   # excluye ETFs/ETPs apalancados
    "Price": "Over $5",
    "Change": "Up 5%",
    "Relative Volume": "Over 1.5",
}
GAPPERS_SHORT_FILTERS = {
    "Average Volume": "Over 500K",
    "Country": "USA",
    "Industry": "Stocks only (ex-Funds)",   # excluye ETFs/ETPs apalancados
    "Price": "Over $5",
    "Change": "Down 5%",
    "Relative Volume": "Over 1.5",
}


# ------------------------------------------------------------------
# Finviz auth (opcional). Inyecta la cookie de sesión (Elite/logueada)
# en la Session global de finvizfinance para evitar el rate-limiting del
# scraping anónimo — la causa de los 'NoneType ... find_all'. El valor lo
# pone el usuario en .env (FINVIZ_AUTH_COOKIE); el código solo lo lee y
# NUNCA lo registra en el log.
# ------------------------------------------------------------------
_FINVIZ_AUTH_APPLIED = False


def _apply_finviz_auth(logger=None) -> bool:
    """Aplica FINVIZ_AUTH_COOKIE a la sesión de finvizfinance una sola vez."""
    global _FINVIZ_AUTH_APPLIED
    if _FINVIZ_AUTH_APPLIED:
        return True
    cookie = os.getenv("FINVIZ_AUTH_COOKIE", "").strip()
    if not cookie:
        return False
    try:
        import finvizfinance.util as fv_util
        fv_util.session.headers.update({"Cookie": cookie})
        _FINVIZ_AUTH_APPLIED = True
        if logger:
            logger.info("Finviz: sesión autenticada vía FINVIZ_AUTH_COOKIE")
        return True
    except Exception as e:
        if logger:
            logger.warning(f"Finviz: no se pudo aplicar la cookie de auth — {e}")
        return False


class FundamentalAnalyst(BaseAgent):
    def __init__(self, client):
        super().__init__(client)
        _apply_finviz_auth(self.logger)

    def run(self, scan_candidates: list, session: str = "morning") -> tuple:
        """
        First filter + discovery phase.
        Returns (enriched_candidates, fund_map: dict[ticker, FundamentalResult])
        """
        top_n = min(len(scan_candidates), FUNDAMENTAL_TOP_N)
        candidates = scan_candidates[:top_n]
        rest = scan_candidates[top_n:]

        self.logger.info(f"FundamentalAnalyst: analyzing {len(candidates)} candidates")
        self._cache = self._load_fundcache()
        cache_hits = 0

        fund_map = {}
        filtered = []

        for cand in candidates:
            try:
                data, from_cache = self._fetch_finviz(cand.ticker)
                if not data:
                    filtered.append(cand)
                    continue

                if from_cache:
                    cache_hits += 1

                result = self._build_result(cand.ticker, data, cand.price)

                if result.blocked:
                    self.logger.info(f"  BLOCKED {cand.ticker}: {result.block_reason}")
                    continue

                if data.get("Company") and data["Company"] not in ("-", ""):
                    cand.company_name = data["Company"]
                if data.get("Sector") and data["Sector"] not in ("-", ""):
                    cand.sector = data["Sector"]

                fund_map[cand.ticker] = result
                filtered.append(cand)
                if not from_cache:
                    time.sleep(0.35)

            except Exception as e:
                self.logger.warning(f"  {cand.ticker}: Finviz fetch error — {e}")
                filtered.append(cand)

        blocked_count = len(candidates) - len(filtered)

        # Descubrimiento de nuevos candidatos. Corre en CADA sesión a propósito:
        # no consume tokens (este agente nunca llama a Claude) y las fases que sí
        # los gastan van topadas por constante (TA_TOP_N/SENTIMENT_TOP_N/RISK_TOP_N),
        # así que descubrir de más no encarece la ejecución — solo cambia qué
        # tickers compiten por esas plazas.
        existing = {c.ticker for c in filtered + rest}
        new_candidates = self._run_screener(existing, fund_map)

        # Screener de gappers: disparos por noticia/evento del día
        gappers = self._run_gappers_screener(existing, fund_map)

        # Guardado al final: así también se cachean los fundamentales de los
        # tickers descubiertos por los screeners (antes se guardaba antes de
        # ellos y se re-descargaban en cada sesión).
        self._save_fundcache(self._cache)

        all_candidates = filtered + new_candidates + gappers + rest
        self.logger.info(
            f"Fundamental complete: {len(filtered)} kept, {blocked_count} blocked, "
            f"{len(new_candidates)} new from screener, {len(gappers)} gappers "
            f"→ {len(all_candidates)} total (caché hits: {cache_hits}/{len(candidates)})"
        )
        return all_candidates, fund_map

    # ------------------------------------------------------------------
    # Data fetching
    # ------------------------------------------------------------------

    def _load_fundcache(self) -> dict:
        path = Path(CONTEXT_DIR) / "fundamentals_cache.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {"tickers": {}}

    def _save_fundcache(self, cache: dict) -> None:
        path = Path(CONTEXT_DIR) / "fundamentals_cache.json"
        tmp  = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")
        os.replace(tmp, path)

    def _cache_fresh(self, entry: dict, ttl_days: int = CACHE_TTL_DAYS) -> bool:
        updated = entry.get("updated")
        if not updated:
            return False
        try:
            age = (datetime.now() - datetime.fromisoformat(updated)).days
            return age < ttl_days
        except Exception:
            return False

    def _fetch_finviz(self, ticker: str) -> tuple:
        """Retorna (data_dict, from_cache: bool). Usa caché si datos < CACHE_TTL_DAYS días."""
        entry = self._cache.get("tickers", {}).get(ticker, {})
        if entry and self._cache_fresh(entry):
            # Siempre refresca si earnings próximos
            days_to_earn = entry.get("data", {}).get("_earnings_days", 999)
            if days_to_earn > EARNINGS_REFRESH_DAYS:
                self.logger.debug(f"  {ticker}: caché OK ({entry.get('updated', '')[:10]})")
                return entry["data"], True

        # Fetch fresco desde Finviz — con reintentos por rate-limiting intermitente.
        # Finviz sirve páginas de bloqueo puntuales bajo carga: la lib revienta con
        # "'NoneType' object has no attribute 'find_all'" al no encontrar la tabla.
        data = self._finviz_with_retry(ticker)

        if not data:
            # Fetch agotado: degrada a caché rancia si existe. Mejor un fundamental
            # algo viejo que perder el score del ticker en todo el run.
            if entry and entry.get("data"):
                self.logger.warning(
                    f"  {ticker}: Finviz no disponible, uso caché rancia "
                    f"({entry.get('updated', '')[:10]})"
                )
                return entry["data"], True
            return {}, False

        earnings_str = data.get("Earnings", "-") or "-"
        data["_earnings_days"] = self._parse_earnings_days(earnings_str)
        self._cache.setdefault("tickers", {})[ticker] = {
            "updated": datetime.now().isoformat(),
            "data": data,
        }
        return data, False

    def _finviz_with_retry(self, ticker: str, attempts: int = 3) -> dict:
        """Scrapea Finviz con reintentos + backoff. Devuelve {} si todos fallan.

        Un error de red es transitorio: un backoff creciente suele resolverlo en
        el 2º/3er intento. Si la página llega bien pero no tiene tablas (ticker
        inválido/página de bloqueo), _scrape_finviz_quote devuelve {} sin excepción
        y no reintentamos.
        """
        for i in range(attempts):
            try:
                return self._scrape_finviz_quote(ticker)
            except Exception as e:
                if i < attempts - 1:
                    wait = 1.5 * (i + 1)
                    self.logger.debug(
                        f"  {ticker}: reintento Finviz {i + 1}/{attempts} "
                        f"tras error ({e}); espero {wait:.1f}s"
                    )
                    time.sleep(wait)
                else:
                    self.logger.warning(
                        f"  {ticker}: Finviz falló tras {attempts} intentos — {e}"
                    )
        return {}

    @staticmethod
    def _scrape_finviz_quote(ticker: str) -> dict:
        """Parsea la página de cotización de Finviz sin depender de
        finvizfinance.ticker_fundament().

        Finviz cambió el layout (jul-2026): eliminó el `div.quote-links` en el
        que la librería 1.3.0 revienta ('NoneType ... find_all'), y partió la
        rejilla de datos en VARIAS tablas `snapshot-table2`. Aquí las combinamos
        todas en un único dict label->valor, que es la forma que espera
        `_build_result`. Reutiliza la sesión de finvizfinance (con la cookie de
        FINVIZ_AUTH_COOKIE si está) y su config de UA/proxy/timeout.

        Sin estado de instancia (no usa self): también la llama
        portfolio_watchdog para chequear earnings de posiciones abiertas sin
        tener que instanciar el agente completo (que exige cliente de Claude).
        """
        import finvizfinance.util as fv_util
        from bs4 import BeautifulSoup

        r = fv_util.session.get(
            "https://finviz.com/quote.ashx",
            params={"t": ticker, "p": "d"},
            headers=fv_util.headers,
            timeout=fv_util.timeout_value,
            proxies=fv_util.proxy_dict,
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")

        tables = soup.find_all("table", class_="snapshot-table2")
        if not tables:
            return {}  # ticker inválido o página inesperada — no reintentar

        data = {}
        for table in tables:
            cells = table.find_all("td")
            for j in range(0, len(cells) - 1, 2):
                key = cells[j].get_text(strip=True)
                if key:
                    data[key] = cells[j + 1].get_text(strip=True)

        company = soup.find("h2", class_="quote-header_ticker-wrapper_company")
        if company:
            data["Company"] = company.get_text(strip=True)
        return data

    @staticmethod
    def _scrape_finviz_screener(filters: dict, limit: int, order: str = "-change") -> list:
        """Devuelve la lista de tickers de un screener de Finviz.

        No usa Overview.screener_view() de finvizfinance 1.3.0: con el layout
        de jul-2026 la celda del ticker lleva DOS enlaces (el del logo, que
        muestra la inicial como placeholder, y el `tab-link` con el ticker).
        La librería lee la celda con `col.text`, que concatena ambos y produce
        basura: ADSK -> AADSK, A -> AA. Eso provocaba 404s en masa y, cuando el
        resultado existía de verdad como ticker, colaba fantasmas en la
        watchlist (A/Agilent -> AA/Alcoa).

        Aquí el ticker se lee del parámetro `t` de la query del enlace, que es
        el dato canónico. Se acepta cualquier ruta (jul-2026 Finviz pasó de
        `quote.ashx?t=` a `stock?t=`), así que sobrevive a otro cambio de ruta.
        Se reutiliza Overview solo para traducir filters_dict -> params de URL,
        y la sesión de finvizfinance (cookie/UA/proxy/timeout), igual que
        `_scrape_finviz_quote`.

        `order` es el token de orden de Finviz (`o=`). NUNCA usar "ticker"
        (alfabético): con `limit` pequeño, el screener solo cosecha AA/AB/AC... y
        la rotación one-in-one-out degeneraba la watchlist a A-F (jul-2026). Se
        ordena por movimiento del día — "-change" (mayores subidas) para largos,
        "change" (mayores caídas) para cortos — para sacar los movers reales, no
        los primeros del abecedario. OJO: un token inválido hace que Finviz caiga
        al orden por defecto (por ticker), reintroduciendo el sesgo; usar solo
        tokens canónicos verificados ("change", "-change", "volume", "-volume").
        """
        import finvizfinance.util as fv_util
        from finvizfinance.screener.overview import Overview
        from bs4 import BeautifulSoup
        from urllib.parse import urlparse, parse_qs

        overview = Overview()
        overview.set_filter(filters_dict=filters)
        params = dict(overview.request_params)
        params["o"] = order

        tickers, seen = [], set()
        for offset in range(1, limit + 1, Overview.size):
            if offset > 1:
                params["r"] = offset

            r = fv_util.session.get(
                overview.url,
                params=params,
                headers=fv_util.headers,
                timeout=fv_util.timeout_value,
                proxies=fv_util.proxy_dict,
            )
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")

            table = soup.find("table", class_="screener_table")
            if not table:
                break

            page_tickers = []
            for row in table.find_all("tr"):
                ticker = ""
                for link in row.find_all("a", href=True):
                    qs = parse_qs(urlparse(link["href"]).query)
                    ticker = (qs.get("t") or [""])[0].strip().upper()
                    if ticker:
                        break
                if ticker and ticker not in seen:
                    seen.add(ticker)
                    page_tickers.append(ticker)

            if not page_tickers:
                break
            tickers.extend(page_tickers)
            if len(tickers) >= limit:
                break

        return tickers[:limit]

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _build_result(self, ticker: str, data: dict, price: float) -> FundamentalResult:
        earnings_str = data.get("Earnings", "-") or "-"
        days_to_earn = self._parse_earnings_days(earnings_str)
        short_float  = self._pf(data.get("Short Float"))
        short_ratio  = self._pf(data.get("Short Ratio"))
        target_price = self._pf(data.get("Target Price"))
        recom        = self._pf(data.get("Recom")) or 3.0
        insider_trans = self._pf(data.get("Insider Trans"))
        inst_trans   = self._pf(data.get("Inst Trans"))
        forward_pe   = self._pf(data.get("Forward P/E"))
        debt_eq      = self._pf(data.get("Debt/Eq"))
        profit_margin = self._pf(data.get("Profit Margin"))
        beta         = self._pf(data.get("Beta")) or 1.0

        risk_flags = []
        blocked = False
        block_reason = ""

        # --- Block conditions ---
        # Filtro B: bloquea si el earnings cae dentro de la ventana de hold
        # (EARNINGS_HOLD_BLOCK_DAYS ⊇ EARNINGS_BLOCK_DAYS). Sostener durante el
        # reporte = riesgo binario de gap ingestionable con stop. Se cuenta también
        # earnings hoy (days_to_earn == 0).
        earn_block_days = max(EARNINGS_BLOCK_DAYS, EARNINGS_HOLD_BLOCK_DAYS)
        if 0 <= days_to_earn <= earn_block_days:
            blocked = True
            block_reason = f"Earnings en {days_to_earn}d ({earnings_str}) dentro de la ventana de hold"

        # --- Fundamental score (0-10) ---
        score = 5.0

        # Analyst consensus: recom 1=Strong Buy → +1.5, 5=Strong Sell → -1.5
        score += (3.0 - recom) * 0.75

        # Analyst target vs price
        if price > 0 and target_price > 0:
            upside = (target_price - price) / price * 100
            if upside > 30:
                score += 1.0
            elif upside > 15:
                score += 0.5
            elif upside < -10:
                score -= 1.0
                risk_flags.append("price_above_analyst_target")

        # Insider activity
        if insider_trans > 10:
            score += 1.0
        elif insider_trans > 0:
            score += 0.3
        elif insider_trans < -50:
            score -= 1.5
            risk_flags.append("heavy_insider_selling")
        elif insider_trans < -20:
            score -= 0.8
            risk_flags.append("insider_selling")

        # Institutional flow
        if inst_trans > 5:
            score += 0.5
        elif inst_trans < -5:
            score -= 0.3

        # Short float (headwind for longs)
        if short_float > 25:
            risk_flags.append(f"very_high_short_float ({short_float:.0f}%)")
            score -= 0.5
        elif short_float > 15:
            risk_flags.append(f"high_short_float ({short_float:.0f}%)")

        # Leverage
        if debt_eq > 3:
            risk_flags.append("high_leverage")
            score -= 0.5

        # Earnings proximity penalty (5-10 days away)
        if 5 < days_to_earn <= 10:
            score -= 0.5

        score = round(max(0.0, min(10.0, score)), 2)

        return FundamentalResult(
            ticker=ticker,
            company_name=data.get("Company", ticker),
            sector=data.get("Sector", ""),
            earnings_date=earnings_str,
            earnings_days_away=days_to_earn,
            earnings_risk=0 < days_to_earn <= 5,
            short_float_pct=short_float,
            short_ratio=short_ratio,
            target_price=target_price,
            analyst_recom=recom,
            insider_trans_pct=insider_trans,
            inst_trans_pct=inst_trans,
            forward_pe=forward_pe,
            debt_equity=debt_eq,
            profit_margin=profit_margin,
            beta=beta,
            fundamental_score=score,
            risk_flags=risk_flags,
            blocked=blocked,
            block_reason=block_reason,
        )

    # ------------------------------------------------------------------
    # Screener — discover new candidates
    # ------------------------------------------------------------------

    def _run_screener(self, existing_tickers: set, fund_map: dict) -> list:
        """Descubre candidatos nuevos vía screeners de Finviz. Corre en cada
        sesión: ver la nota en `run()` sobre por qué no encarece la ejecución.
        """
        new_candidates = []
        promotable = []   # solo candidatos de CALIDAD que pueden entrar en la watchlist
        # order: "-change" saca los mayores movers al alza (largos), "change" los
        # mayores a la baja (cortos). NUNCA "ticker" — ver _scrape_finviz_screener.
        # promotes: si el hallazgo puede PERSISTIR en la watchlist. Solo el screener
        # fundamental de calidad (long_screener) promociona; momentum/técnicos y
        # cortos son efímeros (un buen día no basta para entrar — ver config).
        configs = [
            (LONG_SCREENER_FILTERS,        8, "long_screener",      "-change", True),
            (SHORT_SCREENER_FILTERS,        5, "short_screener",     "change",  False),
            (TA_WEEKLY_LONG_FILTERS,       10, "ta_weekly_long",     "-change", False),
            (TA_MONTHLY_BREAKOUT_FILTERS,   8, "ta_monthly_breakout", "-change", False),
        ]

        for filters, limit, label, order, promotes in configs:
            try:
                for ticker in self._scrape_finviz_screener(filters, limit, order):
                    if ticker in existing_tickers:
                        continue
                    try:
                        fdata, _ = self._fetch_finviz(ticker)
                        price = self._pf(fdata.get("Price"))
                        if price <= 0:
                            continue
                        result = self._build_result(ticker, fdata, price)
                        if result.blocked:
                            continue
                        fund_map[ticker] = result
                        existing_tickers.add(ticker)
                        cand = ScanCandidate(
                            ticker=ticker,
                            company_name=fdata.get("Company", ticker),
                            sector=fdata.get("Sector", "Unknown"),
                            price=price,
                            volume_ratio=1.0,
                            price_change_pct=self._pf(fdata.get("Change")),
                            market_cap=0.0,
                            avg_volume_20d=500_000,
                            high_52w=price,
                            low_52w=price,
                            scan_signals=[label],
                            initial_score=result.fundamental_score * 0.5,
                        )
                        new_candidates.append(cand)
                        # Puerta de calidad: promociona a la watchlist solo si el
                        # screener es de calidad Y el fundamental supera el suelo.
                        if promotes and result.fundamental_score >= WATCHLIST_PROMOTE_MIN_FUND:
                            promotable.append(cand)
                        self.logger.info(f"  Screener new: {ticker} (score {result.fundamental_score})")
                        time.sleep(0.35)
                    except Exception:
                        continue

            except Exception as e:
                self.logger.warning(f"Screener {label} failed: {e}")

        if promotable:
            self._update_watchlist(promotable)

        return new_candidates

    def _run_gappers_screener(self, existing_tickers: set, fund_map: dict) -> list:
        """Descubrimiento DIARIO de valores que se disparan hoy (gappers por noticia/evento).

        Se ejecuta en cada sesión (sin caché semanal), no excluye sobrecompra y NO
        modifica watchlist.json: los gappers son candidatos efímeros del día. Cubre el
        hueco de detectar disparos en tickers que NO están en la watchlist.
        """
        if not GAPPERS_ENABLED:
            return []

        new_candidates = []
        # -change: mayores gaps al alza primero (largos); change: mayores gaps a
        # la baja primero (cortos). Sin esto Finviz ordenaba por ticker (sesgo A-F).
        configs = [
            (GAPPERS_LONG_FILTERS,  "gapper_long",  "long",  "-change"),
            (GAPPERS_SHORT_FILTERS, "gapper_short", "short", "change"),
        ]

        for filters, label, direction, order in configs:
            try:
                for ticker in self._scrape_finviz_screener(filters, GAPPERS_LIMIT, order):
                    if ticker in existing_tickers:
                        continue
                    try:
                        fdata, _ = self._fetch_finviz(ticker)
                        price = self._pf(fdata.get("Price"))
                        if price <= 0:
                            continue
                        result = self._build_result(ticker, fdata, price)
                        if result.blocked:
                            self.logger.info(f"  Gapper BLOCKED {ticker}: {result.block_reason}")
                            continue
                        fund_map[ticker] = result
                        existing_tickers.add(ticker)
                        change_pct = self._pf(fdata.get("Change"))
                        new_candidates.append(ScanCandidate(
                            ticker=ticker,
                            company_name=fdata.get("Company", ticker),
                            sector=fdata.get("Sector", "Unknown"),
                            price=price,
                            volume_ratio=self._pf(fdata.get("Rel Volume")) or 2.0,
                            price_change_pct=change_pct,
                            market_cap=0.0,
                            avg_volume_20d=500_000,
                            high_52w=price,
                            low_52w=price,
                            scan_signals=[label],
                            initial_score=result.fundamental_score * 0.5,
                        ))
                        self.logger.info(
                            f"  Gapper {direction}: {ticker} ({change_pct:+.1f}% hoy, "
                            f"fund {result.fundamental_score})"
                        )
                        time.sleep(0.35)
                    except Exception:
                        continue

            except Exception as e:
                self.logger.warning(f"Gappers screener {label} failed: {e}")

        if new_candidates:
            self.logger.info(f"  Gappers: {len(new_candidates)} candidatos nuevos del día")

        return new_candidates

    # Orígenes protegidos: el núcleo curado por el operador NUNCA se expulsa.
    _PROTECTED_SOURCES = {"manual", "core", "seed", None}

    def _update_watchlist(self, new_candidates: list) -> None:
        """Promoción a la watchlist con puerta de calidad y NÚCLEO PROTEGIDO.

        `new_candidates` ya viene filtrado a candidatos de calidad (ver _run_screener:
        solo long_screener con fundamental_score alto). La rotación mantiene el tamaño
        bajo `WATCHLIST_MAX_SIZE` reciclando solo entradas AUTO-AÑADIDAS más antiguas;
        el núcleo curado (source: manual) es intocable. Así un buen día no desplaza a
        un buen ticker, y la lista no vuelve a degenerar.
        """
        watchlist_path = Path(CONTEXT_DIR) / "watchlist.json"
        try:
            data = json.loads(watchlist_path.read_text(encoding="utf-8"))
        except Exception:
            return

        # Soporte para formato legacy (solo "tickers") y nuevo (con "entries")
        entries = data.get("entries")
        if not entries:
            entries = [
                {"ticker": t, "added": data.get("updated", "2000-01-01"), "source": "manual"}
                for t in data.get("tickers", [])
            ]

        existing = {e["ticker"] for e in entries}
        added = []
        for c in new_candidates:
            if c.ticker in existing:
                continue
            source = c.scan_signals[0] if c.scan_signals else "screener"
            entries.append({
                "ticker": c.ticker,
                "added": datetime.now().strftime("%Y-%m-%d"),
                "source": source,
            })
            existing.add(c.ticker)
            added.append(c.ticker)

        if not added:
            return

        # Recorte por cap: solo si superamos WATCHLIST_MAX_SIZE, y expulsando las
        # entradas AUTO-AÑADIDAS más antiguas (nunca el núcleo curado). Si no hay
        # auto-añadidas que reciclar, se deja crecer antes que tocar un nombre manual.
        removed = []
        overflow = len(entries) - WATCHLIST_MAX_SIZE
        if overflow > 0:
            added_set = set(added)   # nunca reciclar lo recién promocionado
            auto_oldest = sorted(
                (e for e in entries
                 if e.get("source") not in self._PROTECTED_SOURCES and e["ticker"] not in added_set),
                key=lambda e: (e.get("added", ""), e["ticker"]),
            )
            remove = {e["ticker"] for e in auto_oldest[:overflow]}
            if remove:
                entries = [e for e in entries if e["ticker"] not in remove]
                removed = list(remove)

        data["entries"] = entries
        data["tickers"] = [e["ticker"] for e in entries]
        data["updated"] = datetime.now().strftime("%Y-%m-%d")

        tmp = watchlist_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, watchlist_path)

        self.logger.info(
            f"  Watchlist: +{len(added)} calidad ({', '.join(added)})"
            + (f" | -{len(removed)} auto-antiguos ({', '.join(removed)})" if removed else "")
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _pf(self, val) -> float:
        if not val or str(val).strip() in ("-", "", "nan"):
            return 0.0
        try:
            return float(str(val).replace("%", "").replace(",", "").strip())
        except Exception:
            return 0.0

    @staticmethod
    def _parse_earnings_days(earnings_str: str) -> int:
        """Días con signo hasta la fecha de earnings de Finviz: negativo = ya
        ocurrió hace N días, 0 = hoy, positivo = N días para el próximo.

        Bug corregido 2026-07-30 (caso BE): Finviz no incluye año y tarda unos
        días en rotar el campo al próximo trimestre tras el reporte. La lógica
        anterior (`dt < ahora - 1 día → año siguiente`) trataba "Jul 28"
        evaluado el 29/07 (earnings de AYER) como si fuera dentro de 364 días,
        porque comparaba la medianoche de `dt` contra un timestamp con hora.
        Eso desactivó el Filtro B de earnings justo el día de mayor riesgo
        (post-reacción). Ahora se prueban las 3 interpretaciones de año
        (anterior/actual/siguiente) y se toma la más cercana a hoy — sin
        heurística de "más de N días implica año que viene".
        """
        if not earnings_str or earnings_str.strip() in ("-", ""):
            return 999
        try:
            clean = re.sub(r"\s+(AMC|BMO|--)\s*$", "", earnings_str.strip())
            today = datetime.now().date()
            base = datetime.strptime(f"{clean} {today.year}", "%b %d %Y").date()
            candidates = [base.replace(year=today.year + delta) for delta in (-1, 0, 1)]
            closest = min(candidates, key=lambda d: abs((d - today).days))
            return (closest - today).days
        except Exception:
            return 999
