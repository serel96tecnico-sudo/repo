import curl_cffi.requests as ccffi
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

_orig = ccffi.Session.request
def _p(self, m, u, **kw):
    kw.setdefault('verify', False)
    return _orig(self, m, u, **kw)
ccffi.Session.request = _p

import yfinance as yf

candidates = {
    'BMW.DE':  'BMW',
    'ASML.AS': 'ASML',
    'UCG.MI':  'UniCredit',
    'SU.PA':   'Schneider Electric',
    'AIR.PA':  'Airbus',
}

for sym, name in candidates.items():
    try:
        df = yf.Ticker(sym).history(period='90d', auto_adjust=True)
        if df.empty or len(df) < 20:
            print(f'{name}: sin datos')
            continue

        close = df['Close'].squeeze()
        high  = df['High'].squeeze()
        low   = df['Low'].squeeze()

        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()

        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        rsi   = (100 - 100 / (1 + gain / loss))

        tr    = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        atr   = tr.ewm(span=14).mean()

        last      = float(close.iloc[-1])
        prev      = float(close.iloc[-2])
        e20       = float(ema20.iloc[-1])
        e50       = float(ema50.iloc[-1])
        rsi_val   = float(rsi.iloc[-1])
        atr_val   = float(atr.iloc[-1])
        chg       = (last - prev) / prev * 100

        min20 = float(low.iloc[-20:].min())
        max20 = float(high.iloc[-20:].max())

        # Entry: precio actual o en pullback (0.5% debajo)
        entry = last
        sl    = round(min20 - atr_val * 0.3, 2)
        risk  = entry - sl
        tp1   = round(entry + risk * 1.5, 2)
        tp2   = round(entry + risk * 2.5, 2)
        rr    = risk / risk * 1.5  # simplificado, siempre 1.5 TP1

        print(f'\n=== {name} ({sym}) ===')
        print(f'  Precio: {last:.2f} | RSI: {rsi_val:.1f} | Hoy: {chg:+.2f}%')
        print(f'  EMA20: {e20:.2f} | EMA50: {e50:.2f} | ATR: {atr_val:.2f}')
        print(f'  Rango 20d: {min20:.2f} - {max20:.2f}')
        print(f'  SL: {sl:.2f} (-{(entry-sl)/entry*100:.1f}%) | TP1: {tp1:.2f} (+{(tp1-entry)/entry*100:.1f}%) | TP2: {tp2:.2f} (+{(tp2-entry)/entry*100:.1f}%)')
        print(f'  R/R TP1: {(tp1-entry)/(entry-sl):.1f}x | TP2: {(tp2-entry)/(entry-sl):.1f}x')

        if last > e20 and last > e50:
            trend = 'ALCISTA (sobre EMA20 y EMA50)'
        elif last > e50:
            trend = 'NEUTRO (sobre EMA50, bajo EMA20)'
        elif last > e20:
            trend = 'NEUTRO (sobre EMA20, bajo EMA50)'
        else:
            trend = 'BAJISTA'
        print(f'  Tendencia: {trend}')
    except Exception as e:
        print(f'{name}: error - {e}')
