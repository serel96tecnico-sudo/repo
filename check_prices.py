import warnings
warnings.filterwarnings('ignore')

try:
    import curl_cffi.requests as _ccffi
    _orig_req = _ccffi.Session.request
    def _ssl_patched(self, method, url, **kw):
        kw.setdefault('verify', False)
        return _orig_req(self, method, url, **kw)
    _ccffi.Session.request = _ssl_patched
except Exception:
    pass

import yfinance as yf

tickers = {
    'IONQ':   {'bep': 46.43, 'sl': 44.00, 'tp': None,  'cur': 'USD', 'qty': 15},
    'NVO':    {'bep': 44.00, 'sl': 42.50, 'tp': 50.00, 'cur': 'USD', 'qty': 30},
    'STNG':   {'bep': 74.49, 'sl': 79.00, 'tp': 89.00, 'cur': 'USD', 'qty': 8},
    'DLO':    {'bep': 13.82, 'sl': 12.50, 'tp': 14.55, 'cur': 'USD', 'qty': 39},
    'PYPL':   {'bep': 49.32, 'sl': 44.00, 'tp': 56.00, 'cur': 'USD', 'qty': 11},
    'ASTS':   {'bep': 73.84, 'sl': 64.00, 'tp': 83.00, 'cur': 'USD', 'qty': 10},
    'BMW.DE': {'bep': 83.24, 'sl': 79.00, 'tp': 91.00, 'cur': 'EUR', 'qty': 10},
    'RIB.PA': {'bep': 13.12, 'sl': 12.50, 'tp': 15.00, 'cur': 'EUR', 'qty': 40},
    'NGAS.L': {'bep':  5.31, 'sl': None,  'tp':  5.98, 'cur': 'USD', 'qty': 100},
}

print(f"{'Ticker':<8} {'Precio':>8} {'BEP':>8} {'SL':>8} {'TP':>8} {'P/L neto':>10} {'Dist SL':>9} {'Dist TP':>9}")
print('-' * 80)

for tk, d in tickers.items():
    try:
        t = yf.Ticker(tk)
        hist = t.history(period='1d', interval='1m', auto_adjust=True)
        if hist.empty:
            print(f"{tk:<8} {'N/A (sin datos)':>20}")
            continue
        price = float(hist['Close'].iloc[-1])
    except Exception as e:
        print(f"{tk:<8} {'ERROR':>8} {str(e)[:40]}")
        continue

    sym = '€' if d['cur'] == 'EUR' else '$'
    pl = (price - d['bep']) * d['qty']
    dist_sl = ((price - d['sl']) / price * 100) if d['sl'] else None
    dist_tp = ((d['tp'] - price) / price * 100) if d['tp'] else None
    sl_str = f"{dist_sl:+.1f}%" if dist_sl is not None else '—'
    tp_str = f"{dist_tp:+.1f}%" if dist_tp is not None else '—'
    print(f"{tk:<8} {sym}{price:>7.2f} {sym}{d['bep']:>7.2f} {str(d['sl']) if d['sl'] else '—':>8} {str(d['tp']) if d['tp'] else '—':>8} {sym}{pl:>+9.2f} {sl_str:>9} {tp_str:>9}")
