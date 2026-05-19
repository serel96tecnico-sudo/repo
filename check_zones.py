import warnings; warnings.filterwarnings('ignore')
try:
    import curl_cffi.requests as _c
    _o = _c.Session.request
    def _p(self, m, u, **k): k.setdefault('verify', False); return _o(self, m, u, **k)
    _c.Session.request = _p
except: pass
import yfinance as yf

candidates = {
    'NVDA': {'lo': 219.58, 'hi': 221.78, 'sl': 223.11, 'tp': 182.80, 'dir': 'SHORT', 'broker': 'B2'},
    'PSX':  {'lo': 171.64, 'hi': 173.36, 'sl': 170.44, 'tp': 182.94, 'dir': 'LONG',  'broker': 'B1/B2'},
    'HIMS': {'lo': 27.32,  'hi': 27.60,  'sl': 26.07,  'tp': 31.01,  'dir': 'LONG',  'broker': 'B1/B2'},
    'APLD': {'lo': 39.09,  'hi': 39.49,  'sl': 38.86,  'tp': 43.90,  'dir': 'LONG',  'broker': 'B1/B2'},
    'MRAM': {'lo': 38.75,  'hi': 39.13,  'sl': 39.37,  'tp': 10.75,  'dir': 'SHORT', 'broker': 'B2'},
    'CVX':  {'lo': 148.00, 'hi': 152.00, 'sl': 145.00, 'tp': 162.00, 'dir': 'LONG',  'broker': 'B1/B2'},
    'CLSK': {'lo': 18.00,  'hi': 19.50,  'sl': 17.00,  'tp': 24.00,  'dir': 'LONG',  'broker': 'B1/B2'},
    'JD':   {'lo': 36.00,  'hi': 37.50,  'sl': 34.50,  'tp': 42.00,  'dir': 'LONG',  'broker': 'B2'},
}

print(f"{'Ticker':<6} {'Dir':<6} {'Broker':<6} {'Precio':>8}  {'Zona entrada':>17}  Estado")
print('-' * 65)
for tk, d in candidates.items():
    try:
        h = yf.Ticker(tk).history(period='5d', interval='5m')
        if h.empty:
            print(f"{tk:<6} {d['dir']:<6} {d['broker']:<6} {'N/A':>8}")
            continue
        p = float(h['Close'].iloc[-1])
        zona = f"{d['lo']}-{d['hi']}"
        if d['lo'] <= p <= d['hi']:
            estado = ">>> EN ZONA <<<"
        elif d['dir'] == 'LONG' and p > d['hi']:
            estado = f"+{((p-d['hi'])/p*100):.1f}% sobre entrada"
        elif d['dir'] == 'LONG' and p < d['lo']:
            estado = f"{((p-d['lo'])/p*100):.1f}% bajo entrada"
        elif d['dir'] == 'SHORT' and p < d['lo']:
            estado = f"{((p-d['lo'])/p*100):.1f}% bajo entrada"
        else:
            estado = f"+{((p-d['hi'])/p*100):.1f}% sobre entrada"
        print(f"{tk:<6} {d['dir']:<6} {d['broker']:<6} {p:>8.2f}  {zona:>17}  {estado}")
    except Exception as e:
        print(f"{tk:<6} ERROR: {str(e)[:40]}")
