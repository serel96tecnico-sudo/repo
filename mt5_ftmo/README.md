# mt5_ftmo — Experimento de trading algorítmico (MetaTrader 5 / FTMO)

**Mundo separado del pipeline de acciones.** Esto es el experimento de EAs para
un challenge de fondeo (FTMO) — dinero "de mentira" hasta que haya cuenta fondeada
real. NO tiene relación con la cartera real de broker_1/broker_2 que gestiona el
pipeline (eso vive en la raíz del repo: `agents/`, `data/`, `main.py`, `contex/`).

## Contenido

### Expert Advisors (MT5, `.mq5`)
| EA | Instrumento | Estrategia | Estado |
|----|-------------|-----------|--------|
| `XAUUSD_Donchian_MT5.mq5` | XAUUSD (oro) | Donchian 100 breakout, SL 1.5·ATR / TP 3.0·ATR, sin filtro | #1 — validado WF (+0.276R, 6/6 folds OOS). En demo. |
| `US100_ORB_TrendFilter_MT5.mq5` | US100 (Nasdaq) | Opening Range Breakout + filtro tendencia EMA200 diaria | Edge fino; RVOL/VWAP del vídeo refutados. |

El mismo `XAUUSD_Donchian_MT5.mq5` se usa también sobre **GER40.cash** (DAX) como
estrategia #2 (Donchian es agnóstico del símbolo; solo cambia el Magic number).
Los dos, oro + DAX, están descorrelacionados (~0) → diversifican bien.

Guardián FTMO 2-step integrado: pérdida diaria 5% (buffer 4%), total 10% estático
(buffer 8%). Ver cabecera de cada `.mq5`.

### Backtests de validación (Python)
Ejecutar **desde la raíz del repo** (importan código del pipeline vía `sys.path`):
```bash
python mt5_ftmo/backtest_mt4_gold_silver_h1.py   # walk-forward oro/plata/forex (valida el Donchian del oro)
python mt5_ftmo/backtest_orb.py                  # ORB filtrado (QQQ 5m) — ¿RVOL/VWAP sirven?
python mt5_ftmo/backtest_orb_sweep.py            # barrido + walk-forward del ORB
python mt5_ftmo/backtest_strategy2_search.py     # busca la estrategia #2 (índices/cripto/petróleo)
python mt5_ftmo/backtest_portfolio_combo.py      # ¿diversifica el DAX al oro? DD combinado
```

## Nota
Los EAs de MT4 de broker_3 (CFD real, operado manualmente) NO están aquí — siguen
en `contex/` porque son dinero real, no parte de este experimento.
