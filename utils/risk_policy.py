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

from config import (
    HIGH_BETA_CAP_STRONG_UP, HIGH_BETA_CAP_UPTREND,
    HIGH_BETA_CAP_NEUTRAL, HIGH_BETA_CAP_RISKOFF,
    RISK_PCT_STRONG_UP, RISK_PCT_UPTREND,
    RISK_PCT_NEUTRAL, RISK_PCT_RISKOFF,
    DEFAULT_STOP_PCT, MIN_INVEST_PER_TRADE, MAX_INVEST_PER_TRADE,
)

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
