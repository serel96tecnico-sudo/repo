"""Política de riesgo/exposición — implementación de las reglas de
docs/estrategia_riesgo.md (§3-§4).

Clasifica cada ticker en un *tier de beta* (A núcleo / B growth alta beta /
C especulativo) y, para Tier C, en un *sub-tema* (la lección del 5-jun). Sobre
esa clasificación se aplican:

  R1  high_beta_cap()        — cap de exposición a alta beta (B+C) por régimen.
  R2  (en el orchestrator)   — un sub-tema de Tier C no supera SUBTHEME_MAX_PCT
                               del presupuesto de alta beta.
  R3  risk_pct_for_regime()  — riesgo por trade dinámico, inverso al VIX (1-3%).
                               Reemplaza al antiguo half-size fijo.
  R6  open_position_risk()   — riesgo abierto agregado de la cartera (tope en el
                               orchestrator). Freno al clúster correlacionado.

La taxonomía es una SEMILLA editable por el operador. Los nombres no listados se
clasifican por capitalización (fallback): >=100B → A, >=15B → B, resto → C/otros.
Conviene revisarla periódicamente (§5 falsabilidad).
"""

from datetime import date, datetime

from config import (
    HIGH_BETA_CAP_STRONG_UP, HIGH_BETA_CAP_UPTREND,
    HIGH_BETA_CAP_NEUTRAL, HIGH_BETA_CAP_RISKOFF,
    RISK_PCT_STRONG_UP, RISK_PCT_UPTREND,
    RISK_PCT_NEUTRAL, RISK_PCT_RISKOFF,
    DEFAULT_STOP_PCT, MIN_INVEST_PER_TRADE, MAX_INVEST_PER_TRADE,
    RECENT_LOSS_COOLDOWN_DAYS, RECENT_LOSS_MIN_ABS,
    SHORT_BULLISH_CATALYST_MIN,
    LOGS_DIR,
)
from utils.logger import get_logger

logger = get_logger("RiskPolicy", LOGS_DIR)

# ── Tier A — núcleo, beta baja/media (large-caps establecidos) ────────────────
TIER_A = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVO", "CRM", "NOW", "COST",
    "PYPL", "NKE", "PEP", "KO", "MCD", "CVX", "XOM", "PSX", "CMCSA", "LMT",
    "UNH", "HCA", "ENSG", "DLTR", "DPZ", "PZZA", "ADBE", "PANW", "CRWD",
    "GLD", "UBER", "SIRI",
}

# ── Tier B — growth alta beta (large/mid líquido, volátil) ────────────────────
TIER_B = {
    "AMD", "NVDA", "TSLA", "APP", "MRVL", "COIN", "META", "TSM", "ASML",
    "AMAT", "MU", "INTC", "NBIS", "MXL", "AEHR", "MRAM", "AXTI", "SNDK",
    "AVGO", "ARM", "SMCI", "NVMI", "CCJ", "XPEV", "BIDU", "JD", "BYDDY",
    "CELH", "LULU", "ANF", "DXCM",
}

# ── Tier C — especulativo / micro-tema, agrupado por sub-tema (R2) ────────────
TIER_C_SUBTHEMES = {
    "quantum":        {"IONQ", "RGTI", "QBTS", "LWLG"},
    "mineros_cripto": {"MARA", "WULF", "CLSK", "IREN", "CIFR", "RIOT",
                       "HUT", "BTDR", "HIVE", "BITF"},
    "cripto_fin":     {"CRCL", "SBET", "DXYZ", "DFDV"},
    "space_nuclear":  {"RKLB", "LUNR", "MNTS", "SMR", "OKLO", "BE", "EOS", "SES"},
    "health_spec":    {"HIMS", "OSCR", "LMND", "VKTX", "BEAM", "TEM",
                       "TMDX", "ABSI", "ALMS", "HUMA"},
    "meme_spec":      {"OPEN", "GRAB", "RZLV", "UPST", "HCTI", "TSSI", "NOK",
                       "ORKA", "DGXX", "TMQ", "CRML", "HLR", "MVIS", "OUST",
                       "RXT", "STNE", "PAGS", "DLO", "SBET"},
}

# Índice inverso ticker -> sub-tema para Tier C
_C_INDEX = {t: sub for sub, names in TIER_C_SUBTHEMES.items() for t in names}

# Fallback por capitalización (USD) cuando el ticker no está en ninguna lista
_FALLBACK_A_MIN_CAP = 100_000_000_000
_FALLBACK_B_MIN_CAP = 15_000_000_000


def classify_tier(ticker: str, market_cap: float = None,
                  sector: str = None, short_float: float = None) -> tuple:
    """Devuelve (tier, subtheme). subtheme es None salvo en Tier C.

    Las listas explícitas mandan. Si el ticker no está listado se usa el
    fallback por capitalización; sin capitalización conocida → Tier C 'otros'
    (la elección conservadora: ante la duda, frena lo especulativo)."""
    t = (ticker or "").upper()
    if t in TIER_A:
        return ("A", None)
    if t in TIER_B:
        return ("B", None)
    if t in _C_INDEX:
        return ("C", _C_INDEX[t])

    # Fallback data-driven
    if short_float is not None and short_float >= 0.20:
        return ("C", "otros")
    if market_cap:
        if market_cap >= _FALLBACK_A_MIN_CAP:
            return ("A", None)
        if market_cap >= _FALLBACK_B_MIN_CAP:
            return ("B", None)
        return ("C", "otros")
    return ("C", "otros")


def is_high_beta(tier: str) -> bool:
    """Tier B o C cuentan para el cap de alta beta (R1)."""
    return tier in ("B", "C")


def is_explicitly_classified(ticker: str) -> bool:
    """True si el ticker está en una lista explícita (no vía fallback). Se usa
    para los ETFs: solo cuentan como alta beta si están listados a propósito
    (un ETF de materias primas/amplio es diversificador, no riesgo de nombre)."""
    t = (ticker or "").upper()
    return t in TIER_A or t in TIER_B or t in _C_INDEX


def high_beta_cap(market_conditions) -> float:
    """R1 — cap de exposición a alta beta según régimen. Toma la lectura más
    defensiva entre tendencia y VIX (p.ej. VIX>=20 fuerza el cap NEUTRAL aunque
    el SPY siga en Strong Uptrend)."""
    if market_conditions is None:
        return HIGH_BETA_CAP_UPTREND
    vix = getattr(market_conditions, "vix_level", None) or 0.0
    spy = getattr(market_conditions, "spy_trend", "") or ""
    regime = (getattr(market_conditions, "regime", "") or "").upper()

    if vix > 25 or "Downtrend" in spy or regime.startswith("BEAR"):
        return HIGH_BETA_CAP_RISKOFF
    if vix >= 20 or spy in ("Sideways", "Pullback") or regime.startswith("NEUTRAL"):
        return HIGH_BETA_CAP_NEUTRAL
    if vix >= 18 or spy == "Uptrend":
        return HIGH_BETA_CAP_UPTREND
    return HIGH_BETA_CAP_STRONG_UP


def risk_pct_for_regime(market_conditions) -> float:
    """R3 (redefinida) — riesgo por trade dinámico, INVERSO al VIX. Más riesgo en
    calma (momentum funciona), menos en estrés (los breakouts fallan). Usa la misma
    lectura defensiva que high_beta_cap (toma lo más prudente entre tendencia y VIX)."""
    if market_conditions is None:
        return RISK_PCT_UPTREND
    vix = getattr(market_conditions, "vix_level", None) or 0.0
    spy = getattr(market_conditions, "spy_trend", "") or ""
    regime = (getattr(market_conditions, "regime", "") or "").upper()

    if vix > 25 or "Downtrend" in spy or regime.startswith("BEAR"):
        return RISK_PCT_RISKOFF
    if vix >= 20 or spy in ("Sideways", "Pullback") or regime.startswith("NEUTRAL"):
        return RISK_PCT_NEUTRAL
    if vix >= 18 or spy == "Uptrend":
        return RISK_PCT_UPTREND
    return RISK_PCT_STRONG_UP


def size_position(target_risk: float, risk_per_share: float, entry: float,
                  max_position_value: float) -> int:
    """Nº de acciones: el motor de riesgo (R3) propone y la banda de inversión
    bruta [MIN_INVEST_PER_TRADE, MAX_INVEST_PER_TRADE] acota.

    Orden: (1) base por riesgo = target_risk / riesgo_por_acción;
    (2) techo de banda — recortar si la inversión bruta supera el máximo;
    (3) suelo de banda — subir acciones enteras hasta alcanzar el mínimo (la
        INVERSIÓN MANDA, opción 'b': el suelo se respeta aunque el riesgo suba del
        % del régimen — el tope agregado R6 sigue siendo el límite duro);
    (4) techo absoluto de capital = max_position_value (MAX_POSITION_PCT).

    Ej.: acción de €400 → 1 acc (€400) < suelo €500 → 2 acc (€800)."""
    if risk_per_share <= 0 or entry <= 0:
        return 0
    shares = max(1, int(target_risk / risk_per_share))         # (1) base por riesgo
    if shares * entry > MAX_INVEST_PER_TRADE:                   # (2) techo de banda
        shares = max(1, int(MAX_INVEST_PER_TRADE / entry))
    while shares * entry < MIN_INVEST_PER_TRADE:               # (3) suelo de banda
        shares += 1
    cap = max(1, int(max_position_value / entry))               # (4) tope de capital
    return max(1, min(shares, cap))


def open_position_risk(portfolio) -> float:
    """R6 — riesgo abierto agregado ($) de la cartera = Σ (distancia al stop × acciones).

    Por posición: riesgo = (precio − stop)·qty en longs, (stop − precio)·qty en cortos.
    Si una posición NO tiene stop colocado se asume un stop por defecto a DEFAULT_STOP_PCT
    (decisión del operador, opción 'a': contar el riesgo real, no ignorarlo). Una posición
    en verde con el stop ya sobre el precio (ganancia asegurada) aporta riesgo 0.

    Nota: como en R1/R2, los valores de cartera vienen en USD y PORTFOLIO_VALUE en EUR;
    se tratan a la par (~paridad), simplificación heredada del cálculo de exposición."""
    if not portfolio:
        return 0.0
    total = 0.0
    for p in portfolio.get("acciones", []) + portfolio.get("etfs", []):
        if not isinstance(p, dict):
            logger.warning(f"open_position_risk: entrada malformada en acciones/etfs ignorada: {p!r}")
            continue
        qty = p.get("cantidad", 0) or 0
        px = p.get("precio_actual_usd") or p.get("bep_usd") or 0
        if qty <= 0 or px <= 0:
            continue
        stop = p.get("stop_loss")
        direction = (p.get("direccion") or "long").lower()
        if stop and stop > 0:
            rps = (stop - px) if direction == "short" else (px - stop)
        else:
            rps = px * DEFAULT_STOP_PCT  # sin stop → opción (a): stop por defecto
        if rps > 0:
            total += rps * qty
    return round(total, 2)


def _trade_net_pl(t: dict) -> float:
    """P&L neto de un cierre, en la moneda que traiga (prioriza neto sobre bruto)."""
    for k in ("net_pl_eur", "net_pl_usd", "gross_pl_eur", "gross_pl_usd", "pl"):
        v = t.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def recent_loss_cooldown(portfolio: dict, today=None,
                         window_days: int = RECENT_LOSS_COOLDOWN_DAYS,
                         min_abs: float = RECENT_LOSS_MIN_ABS) -> dict:
    """R7 — Tickers que cerraron en PÉRDIDA (> min_abs) en los últimos `window_days`
    días de calendario. Fuente: portfolio.json['cerradas_semana'].

    Devuelve {TICKER: {"direction": "long"|"short"|None, "days_ago": int,
    "pl": float, "fecha": "YYYY-MM-DD"}} con el cierre perdedor MÁS RECIENTE por
    ticker. `direction` None cuando el registro no la trae (cierres antiguos): en
    ese caso el veto se aplica a cualquier dirección (conservador).

    Sirve para vetar la re-entrada en la misma dirección: un nombre recién stopeado
    que se vuelve a recomendar a los pocos días es el patrón que más pérdidas repite
    (análisis de selección jul-2026: GRAB ×3, MRVL, INTC).
    """
    if not portfolio:
        return {}
    ref = today or date.today()
    if isinstance(ref, datetime):
        ref = ref.date()

    out: dict = {}
    for t in portfolio.get("cerradas_semana", []):
        if not isinstance(t, dict):
            logger.warning(f"recent_loss_cooldown: entrada malformada en cerradas_semana ignorada: {t!r}")
            continue
        pl = _trade_net_pl(t)
        if pl >= 0 or abs(pl) < min_abs:
            continue  # ganador o scratch → no enfría
        fecha_str = (t.get("fecha_cierre") or t.get("fecha") or "")[:10]
        try:
            fecha = datetime.strptime(fecha_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        days_ago = (ref - fecha).days
        if days_ago < 0 or days_ago > window_days:
            continue
        tkr = (t.get("ticker") or "").upper()
        if not tkr:
            continue
        direccion = (t.get("direccion") or "").lower()
        direction = direccion if direccion in ("long", "short") else None
        prev = out.get(tkr)
        if prev is None or days_ago < prev["days_ago"]:
            out[tkr] = {"direction": direction, "days_ago": days_ago,
                        "pl": round(pl, 2), "fecha": fecha_str}
    return out


def short_bullish_catalyst_guard(sentiment_score_normalized: float, catalyst_found: bool,
                                  min_score: float = SHORT_BULLISH_CATALYST_MIN) -> bool:
    """R8 — un corto no se abre contra un catalizador de sentiment fuerte y reciente.

    Caso BE (29/07/2026): corto abierto un día después de un earnings-beat con
    guidance al alza (sentiment_score_normalized 8.9, catalyst_found=True) — el
    sentiment ya había detectado el catalizador pero nada lo convertía en veto.
    Resultado: short squeeze de +25% en 24h, stop saltado con fuerte slippage.

    True → degradar el corto a WATCH (mismo tratamiento duro que el resto de R4).
    """
    return catalyst_found and sentiment_score_normalized >= min_score


def build_tier_map(candidates) -> dict:
    """{ticker: (tier, subtheme)} a partir de ScanCandidate (usa market_cap/sector)."""
    tier_map = {}
    for c in candidates or []:
        tkr = getattr(c, "ticker", None)
        if not tkr:
            continue
        tier_map[tkr.upper()] = classify_tier(
            tkr,
            market_cap=getattr(c, "market_cap", None),
            sector=getattr(c, "sector", None),
        )
    return tier_map
