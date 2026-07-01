import numpy as np
import pandas as pd


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def calculate_macd(
    prices: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple:
    ema_fast = prices.ewm(span=fast, adjust=False).mean()
    ema_slow = prices.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_ema(prices: pd.Series, span: int) -> pd.Series:
    return prices.ewm(span=span, adjust=False).mean()


def calculate_sma(prices: pd.Series, window: int) -> pd.Series:
    return prices.rolling(window=window).mean()


def calculate_bollinger_bands(
    prices: pd.Series, window: int = 20, num_std: float = 2.0
) -> tuple:
    middle = calculate_sma(prices, window)
    std = prices.rolling(window=window).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    pct_b = (prices - lower) / (upper - lower).replace(0, np.nan)
    return upper, middle, lower, pct_b


def calculate_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(com=period - 1, min_periods=period).mean()


def calculate_adx(
    high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
) -> pd.Series:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)

    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    dm_plus = high - prev_high
    dm_minus = prev_low - low
    dm_plus = dm_plus.where((dm_plus > dm_minus) & (dm_plus > 0), 0.0)
    dm_minus = dm_minus.where((dm_minus > dm_plus) & (dm_minus > 0), 0.0)

    atr = tr.ewm(com=period - 1, min_periods=period).mean()
    di_plus = 100 * dm_plus.ewm(com=period - 1, min_periods=period).mean() / atr.replace(0, np.nan)
    di_minus = 100 * dm_minus.ewm(com=period - 1, min_periods=period).mean() / atr.replace(0, np.nan)

    dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.ewm(com=period - 1, min_periods=period).mean()
    return adx


def find_support_resistance(
    prices: pd.Series, window: int = 5, tolerance: float = 0.01
) -> tuple:
    current_price = prices.iloc[-1]
    highs = prices.rolling(window=window * 2 + 1, center=True).max()
    lows = prices.rolling(window=window * 2 + 1, center=True).min()

    pivot_highs = prices[(prices == highs) & (prices.notna())].dropna().unique()
    pivot_lows = prices[(prices == lows) & (prices.notna())].dropna().unique()

    resistance = sorted(
        [p for p in pivot_highs if p > current_price * (1 + tolerance)]
    )[:5]
    support = sorted(
        [p for p in pivot_lows if p < current_price * (1 - tolerance)], reverse=True
    )[:5]

    return support, resistance


def ma200_position(df: pd.DataFrame, period: int = 200) -> dict:
    """Lectura de la media simple de 200 sesiones (SMA200) sobre un OHLCV.

    Devuelve la MA200, la distancia del precio a ella (%), si el precio está por
    encima y si la propia media tiene pendiente alcista (~1 mes). Si hay menos de
    `period` barras devuelve None en lugar de una MA200 sesgada/falsa — así nunca
    reportamos una media larga que no tiene datos suficientes detrás."""
    close = df["Close"].dropna() if (df is not None and not df.empty and "Close" in df) else pd.Series(dtype=float)
    bars = len(close)
    if bars < period:
        return {"ma200": None, "price_vs_ma200_pct": None, "above": None, "slope_up": None, "bars": bars}

    sma = close.rolling(window=period).mean()
    ma = float(sma.iloc[-1])
    price = float(close.iloc[-1])
    sma_valid = sma.dropna()
    # Pendiente sobre ~21 barras (≈1 mes en diario / ~4-5 meses en semanal): tendencia de la media
    ma_prev = float(sma_valid.iloc[-21]) if len(sma_valid) >= 21 else float(sma_valid.iloc[0])
    return {
        "ma200": round(ma, 4),
        "price_vs_ma200_pct": round((price - ma) / ma * 100, 2) if ma else None,
        "above": price > ma,
        "slope_up": ma > ma_prev,
        "bars": bars,
    }


def ma200_confluence_modifier(ma200: dict, direction: str = "long", cap: float = 1.5) -> float:
    """Modificador al ta_score por confluencia de la MA200 en los tres marcos.

    Alineado con la estrategia ("trend alignment / require strength, don't buy
    weakness"): para LONGS, premia precio por encima de las MA200 (tendencia mayor
    a favor) y penaliza comprar por debajo de la MA200 diaria/semanal; para SHORTS,
    invierte la lógica. Marcos sin datos suficientes (None) no votan.

    Voto por marco: +1 si el precio está del lado correcto, -1 si del contrario.
    Bonus de pendiente cuando la media acompaña. Resultado escalado a [-cap, +cap].
    """
    frames = ["weekly", "daily", "4h"]
    votes = 0.0
    counted = 0
    for f in frames:
        rd = ma200.get(f) or {}
        above = rd.get("above")
        if above is None:
            continue
        counted += 1
        favorable = above if direction == "long" else (not above)
        votes += 1.0 if favorable else -1.0
        slope_up = rd.get("slope_up")
        if slope_up is not None:
            slope_ok = slope_up if direction == "long" else (not slope_up)
            votes += 0.34 if slope_ok else -0.34

    if counted == 0:
        return 0.0
    # Normaliza por el máximo posible (1.34 por marco) y escala al cap
    raw = votes / (counted * 1.34)
    return round(max(-cap, min(cap, raw * cap)), 2)


def calculate_all_indicators(df: pd.DataFrame) -> dict:
    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    rsi = calculate_rsi(close)
    macd_line, signal_line, histogram = calculate_macd(close)
    ema9 = calculate_ema(close, 9)
    ema21 = calculate_ema(close, 21)
    ema50 = calculate_ema(close, 50)
    ema200 = calculate_ema(close, 200)
    sma20 = calculate_sma(close, 20)
    sma50 = calculate_sma(close, 50)
    bb_upper, bb_mid, bb_lower, bb_pct_b = calculate_bollinger_bands(close)
    atr = calculate_atr(high, low, close)
    adx = calculate_adx(high, low, close)
    vol_sma20 = calculate_sma(volume, 20)
    support, resistance = find_support_resistance(close)

    last = lambda s: round(float(s.iloc[-1]), 4) if not pd.isna(s.iloc[-1]) else None
    prev = lambda s: round(float(s.iloc[-2]), 4) if len(s) > 1 and not pd.isna(s.iloc[-2]) else None

    price = last(close)
    vol_ratio = round(float(volume.iloc[-1]) / float(vol_sma20.iloc[-1]), 2) if last(vol_sma20) else 1.0

    return {
        "price": price,
        "price_prev": prev(close),
        "volume": int(volume.iloc[-1]),
        "volume_ratio_20d": vol_ratio,
        "rsi_14": last(rsi),
        "rsi_prev": prev(rsi),
        "macd": last(macd_line),
        "macd_signal": last(signal_line),
        "macd_histogram": last(histogram),
        "macd_histogram_prev": prev(histogram),
        "ema9": last(ema9),
        "ema21": last(ema21),
        "ema50": last(ema50),
        "ema200": last(ema200),
        "sma20": last(sma20),
        "sma50": last(sma50),
        "bb_upper": last(bb_upper),
        "bb_middle": last(bb_mid),
        "bb_lower": last(bb_lower),
        "bb_pct_b": last(bb_pct_b),
        "atr_14": last(atr),
        "adx_14": last(adx),
        "support_levels": [round(s, 2) for s in support],
        "resistance_levels": [round(r, 2) for r in resistance],
        "high_52w": round(float(high.tail(252).max()), 2),
        "low_52w": round(float(low.tail(252).min()), 2),
    }


def score_technical_setup(indicators: dict) -> float:
    score = 0.0
    rsi = indicators.get("rsi_14") or 50
    rsi_prev = indicators.get("rsi_prev") or 50

    if 30 <= rsi <= 40 and rsi > rsi_prev:
        score += 2.0
    elif 40 < rsi <= 60:
        score += 1.5
    elif 60 < rsi <= 70:
        score += 1.0
    elif rsi < 30:
        score += 0.5

    macd_hist = indicators.get("macd_histogram") or 0
    macd_hist_prev = indicators.get("macd_histogram_prev") or 0
    macd = indicators.get("macd") or 0
    macd_signal = indicators.get("macd_signal") or 0

    if macd_hist > 0 and macd_hist > macd_hist_prev:
        score += 2.0
    elif macd > macd_signal and macd_hist > 0:
        score += 1.0

    price = indicators.get("price") or 0
    ema9 = indicators.get("ema9") or 0
    ema21 = indicators.get("ema21") or 0
    ema50 = indicators.get("ema50") or 0

    if price > ema9 > ema21 > ema50:
        score += 3.0
    elif price > ema21 > ema50:
        score += 2.0
    elif price > ema50:
        score += 1.0

    vol_ratio = indicators.get("volume_ratio_20d") or 1.0
    if vol_ratio >= 2.0:
        score += 1.5
    elif vol_ratio >= 1.5:
        score += 1.0

    adx = indicators.get("adx_14") or 0
    if adx >= 30:
        score += 1.5
    elif adx >= 20:
        score += 0.75

    return round(min(score, 10.0), 2)
