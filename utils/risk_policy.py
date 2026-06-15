"""Política de riesgo/exposición — implementación de las reglas de
docs/estrategia_riesgo.md (§3-§4).

Clasifica cada ticker en un *tier de beta* (A núcleo / B growth alta beta /
C especulativo) y, para Tier C, en un *sub-tema* (la lección del 5-jun). Sobre
esa clasificación se aplican:

  R1  high_beta_cap()        — cap de exposición a alta beta (B+C) por régimen.
  R2  (en el orchestrator)   — un sub-tema de Tier C no supera SUBTHEME_MAX_PCT
                               del presupuesto de alta beta.
  R3  is_neutral_high_vol()  — half-size de nuevos longs Tier C en NEUTRAL+VIX↑.

La taxonomía es una SEMILLA editable por el operador. Los nombres no listados se
clasifican por capitalización (fallback): >=100B → A, >=15B → B, resto → C/otros.
Conviene revisarla periódicamente (§5 falsabilidad).
"""

from config import (
    HIGH_BETA_CAP_STRONG_UP, HIGH_BETA_CAP_UPTREND,
    HIGH_BETA_CAP_NEUTRAL, HIGH_BETA_CAP_RISKOFF,
    NEUTRAL_VIX_THRESHOLD,
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
    if vix >= 20 or "Sideways" in spy or regime.startswith("NEUTRAL"):
        return HIGH_BETA_CAP_NEUTRAL
    if vix >= 18 or spy == "Uptrend":
        return HIGH_BETA_CAP_UPTREND
    return HIGH_BETA_CAP_STRONG_UP


def is_neutral_high_vol(market_conditions) -> bool:
    """R3 — condición de half-size: régimen NEUTRAL/Sideways y VIX >= umbral."""
    if market_conditions is None:
        return False
    vix = getattr(market_conditions, "vix_level", None) or 0.0
    spy = getattr(market_conditions, "spy_trend", "") or ""
    qqq = getattr(market_conditions, "qqq_trend", "") or ""
    regime = (getattr(market_conditions, "regime", "") or "").upper()
    neutralish = (
        regime.startswith("NEUTRAL")
        or "Sideways" in spy
        or "Sideways" in qqq
    )
    return neutralish and vix >= NEUTRAL_VIX_THRESHOLD


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
