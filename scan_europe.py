import pandas as pd
import warnings
warnings.filterwarnings('ignore')

import curl_cffi.requests as ccffi
_orig_request = ccffi.Session.request
def _patched_request(self, method, url, **kw):
    kw.setdefault('verify', False)
    return _orig_request(self, method, url, **kw)
ccffi.Session.request = _patched_request

import yfinance as yf

tickers_dict = {
    'IFX.DE':    'Infineon (Semis)',
    'SAP.DE':    'SAP (Software)',
    'SIE.DE':    'Siemens (Industrial)',
    'ALV.DE':    'Allianz (Seguros)',
    'ASML.AS':   'ASML (Litografia)',
    'MC.PA':     'LVMH (Lujo)',
    'SU.PA':     'Schneider Electric',
    'RHM.DE':    'Rheinmetall (Defensa)',
    'AIR.PA':    'Airbus',
    'TTE.PA':    'TotalEnergies',
    'OR.PA':     'LOreal',
    'DSY.PA':    'Dassault Systemes',
    'HO.PA':     'Thales (Defensa)',
    'NOVO-B.CO': 'Novo Nordisk',
    'MRK.DE':    'Merck KGaA',
    'ENR.DE':    'Siemens Energy',
    'BMW.DE':    'BMW',
    'BAS.DE':    'BASF',
    'DBK.DE':    'Deutsche Bank',
    'UCG.MI':    'UniCredit',
    'RACE.MI':   'Ferrari',
    'ENI.MI':    'ENI',
    'ABI.BR':    'AB InBev',
}

results = []
for sym, name in tickers_dict.items():
    try:
        df = yf.Ticker(sym).history(period='90d', auto_adjust=True)
        if df is None or df.empty or len(df) < 20:
            continue
        close = df['Close']
        vol   = df['Volume']
        high  = df['High']
        low   = df['Low']

        ema20 = close.ewm(span=20).mean()
        ema50 = close.ewm(span=50).mean()

        delta = close.diff()
        gain  = delta.clip(lower=0).ewm(com=13, adjust=False).mean()
        loss  = (-delta.clip(upper=0)).ewm(com=13, adjust=False).mean()
        rsi   = 100 - (100 / (1 + gain / loss))

        tr    = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
        atr   = tr.ewm(span=14).mean()

        vol_avg = vol.rolling(20).mean()
        vr = float(vol.iloc[-1]) / float(vol_avg.iloc[-1]) if float(vol_avg.iloc[-1]) > 0 else 1.0

        last = float(close.iloc[-1])
        e20  = float(ema20.iloc[-1])
        e50  = float(ema50.iloc[-1])
        r    = float(rsi.iloc[-1])
        d1   = (float(close.iloc[-1]) - float(close.iloc[-2])) / float(close.iloc[-2]) * 100
        d5   = (float(close.iloc[-1]) - float(close.iloc[-6])) / float(close.iloc[-6]) * 100

        if last > e20 and last > e50:
            trend = 'ALCISTA'
        elif last < e20 and last < e50:
            trend = 'BAJISTA'
        else:
            trend = 'NEUTRO'

        results.append({'sym': sym, 'name': name, 'price': last, 'rsi': r,
                        'd1': d1, 'd5': d5, 'vol': vr, 'trend': trend,
                        'atr': float(atr.iloc[-1]), 'e20': e20, 'e50': e50})
    except Exception as e:
        print(f'  {sym}: {e}')

results.sort(key=lambda x: x['d1'], reverse=True)
print(f"\n{'SYM':12} {'NOMBRE':20} {'PRECIO':>8} {'RSI':>5} {'1D%':>7} {'5D%':>7} {'VOL':>5}  TENDENCIA")
print('-' * 82)
for r in results:
    print(f"{r['sym']:12} {r['name']:20} {r['price']:8.2f} {r['rsi']:5.1f} {r['d1']:+7.2f}% {r['d5']:+7.2f}% {r['vol']:5.2f}x  {r['trend']}")
