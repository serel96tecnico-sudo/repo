# Notas persistentes de Claude — memoria operativa

> Memoria manual y fiable entre sesiones para el trading-agent. La mantiene Claude.
> Se carga siempre porque `CLAUDE.md` la referencia. Aquí van workflows, decisiones y
> gotchas que conviene tener a mano en **todas** las sesiones, sin depender de claude-mem.
>
> Reglas para Claude: añadir aquí lo importante de forma proactiva; notas en español;
> fechar cada entrada; mantenerlo conciso (podar lo que quede obsoleto).

---

## 2026-07-27 — Fix sesgo A-F + filtros de calidad (NO revertir)

Diagnóstico: el pipeline solo recomendaba tickers A-F. Causa raíz doble, ya corregida:

1. **Sesgo del screener de descubrimiento** — `_scrape_finviz_screener` ordenaba `o=ticker`
   (alfabético) y cogía los primeros N → siempre cosechaba AA/AB/AC. Ahora ordena por
   movimiento del día (`-change` largos / `change` cortos), configurable por screener.
   OJO: un token `o=` inválido hace que Finviz caiga al orden por defecto (ticker) y
   reintroduce el sesgo — usar solo tokens verificados.
2. **La rotación one-in-one-out** persistía momentum en la watchlist y expulsaba el núcleo
   curado por antigüedad → la lista degeneró a A-F (0 supervivientes manuales el 27/07).
   Watchlist restaurada desde `4bdec2b` (108 tickers, A-X, 100 manuales).

**Nueva política de watchlist (decisión operador): crecimiento con puerta de CALIDAD.**
La watchlist almacena buenos tickers, no nombres que tuvieron un buen día. Solo el
`long_screener` (analyst Strong Buy + insider) promociona, y solo con
`fundamental_score >= WATCHLIST_PROMOTE_MIN_FUND` (7.0). Momentum/técnicos/cortos se
analizan HOY pero NO persisten (efímeros, como gappers). El núcleo `source: manual`
**nunca** se expulsa; la rotación solo recicla auto-añadidos por encima de `WATCHLIST_MAX_SIZE` (120).

**Filtros de selección nuevos (config.py, doc en estrategia_riesgo.md §8):**
- **A — Suelo de score absoluto**: si nada es accionable (todo WATCH), el report titula
  "SIN SETUPS ACCIONABLES HOY" en vez de emitir WATCH de relleno (anti-overtrading).
- **B — Earnings dentro del hold** (`EARNINGS_HOLD_BLOCK_DAYS=12`): bloquea si el earnings
  cae en la ventana de hold (5-10 sesiones), no solo ≤3d. Riesgo binario de gap.
- **C — Suelo de ADX** (`ADX_TREND_MIN=20`): degrada a WATCH breakouts sin tendencia
  establecida (`orchestrator._apply_trend_strength_gate`).
- **D — Confirmación multi-agente**: PENDIENTE, requiere backtest antes de cablear.
- **Beta en descubrimiento** (`SCREENER_MIN_BETA="Over 1"`): los 4 screeners filtran nombres
  lentos (EWS 0.53, BANC 0.74). Solo descubrimiento, no la watchlist curada; no en gappers.
  Verificado que finvizfinance acepta el string ("Over 1"->ta_beta_o1). OJO: no caza laterales
  de beta normal (BRKR 1.28) — eso es el filtro C (ADX).

**Otros:** high_52w/low_52w ahora son de 52 semanas de verdad (scanner 45d→370d, TA 90d→1y).
El summary del report ahora lleva prefijo `[WATCH - no accionable: motivo]` y `demotion_reason`
sobrevive al ReportWriter (antes el texto sonaba a compra con veredicto WATCH).

---

## Workflow "cierre del día"

Cuando el operador manda capturas de sus brokers y dice **"cierre del día"**, actualizar a mano dos ficheros en `contex/`:

1. **`portfolio.json`**
   - `last_updated` → fecha de hoy.
   - `cambios` → resumen del día en español (actividad, entradas/salidas, señales, riesgos R1/R2/R6/R7).
   - `brokers.broker_1` / `broker_2` → balances de la captura.
   - `acciones` → reconstruir posiciones: quitar las cerradas, añadir las nuevas, actualizar `precio_actual_usd`/`net_pl_usd`/`gp_potencial_*`/notas de las que siguen. Cuando Colmex no da precio, estimarlo desde el P/L y las acciones.
   - `cerradas_semana` → añadir las cerradas del día **al principio** del array.
2. **`trades_historico.json`** → añadir cada trade cerrado al array `trades`.

Al terminar, **validar JSON**: `python -c "import json; json.load(open('contex/portfolio.json', encoding='utf-8'))"` (ídem trades_historico).
Los netos en EUR que no vengan del extracto del broker se marcan como **ESTIMADO**.

## Mapeo de brokers

- **broker_1** = DeGiro (EUR), long-only, europeo + US.
- **broker_2** = Colmex Pro, cuenta **COLH50450** (USD), long + short, NYSE/NASDAQ. Los cortos solo van aquí.
- **broker_3** = FPMTrading (CFDs micro-lotes: oro, plata, índices). **Operado MANUAL**; el pipeline NO genera recomendaciones ni sizing para él. **De momento IGNORARLO en los cierres del día — no preguntar por su captura** (indicación del operador 2026-07-22).

## Setup de claude-mem (MEM) — estado y arreglo

- claude-mem v13.5.5 instalado a nivel de usuario. Datos en `~/.claude-mem/` (DB `claude-mem.db`, `projectId` = `trading-agent`).
- **Runtime = `worker`** → captura automática + inyección al inicio de sesión. En este modo NO existe guardado manual (`memory_add`/`observation_add` exigen runtime `server-beta`). La escritura manual "importante" se hace en ESTE fichero, no en MEM.
- El worker necesita **Bun** (`~/.bun/bin/bun.exe`, instalado 2026-07-22). Sin Bun, el worker muere y no graba (pasó del 13-jun al 22-jul: hueco no recuperable).
- Reinicio manual del worker si vuelve a fallar:
  ```bash
  export PATH="$HOME/.bun/bin:$PATH" && bun "C:/Users/bucki/.claude/plugins/cache/thedotmack/claude-mem/13.5.5/scripts/worker-cli.js" restart
  ```
  El CLI puede soltar "Process died during startup" (falso negativo por timeout del health-check en Windows); comprobar de verdad con `curl.exe -s http://127.0.0.1:37777/api/health`.
- Pendiente (opcional): la búsqueda semántica (Chroma) no conecta; solo hay fallback por keyword. No bloqueante.

---

## Registro de decisiones / gotchas

- **2026-07-22** — Creado este fichero (opción A) como memoria persistente fiable, cargada vía `CLAUDE.md`. claude-mem queda como capa automática de fondo tras reactivar su worker con Bun.

## 2026-07-30 — Caso BE: corto contra earnings-beat, R8 nueva + fix de bug de fechas (NO revertir)

Post-mortem del cierre de BE (corto abierto 29/07 @$174,68, cubierto 30/07 @$205,28, net ≈ −$127,40):

1. **Bug corregido** — `FundamentalAnalyst._parse_earnings_days` calculaba `earnings_days_away=364`
   para un earnings de **ayer** ("Jul 28 AMC" evaluado el 29/07), por comparar la medianoche de
   la fecha parseada contra un timestamp con hora (`dt < ahora - 1 día → año siguiente`). Ahora
   prueba las 3 interpretaciones de año (anterior/actual/siguiente) y toma la más cercana a hoy.
2. **R8 nueva** — `short_bullish_catalyst_guard()` en `utils/risk_policy.py`: si el sentiment
   analyst encuentra un catalizador reciente (`catalyst_found=True`) con
   `sentiment_score_normalized >= SHORT_BULLISH_CATALYST_MIN` (7.5), el corto se degrada a WATCH.
   BE tenía sentiment 8.9 con catalyst_found=True (earnings beat + guidance al alza) — el sistema
   ya veía el riesgo de squeeze en el texto del risk_manager, pero nada lo convertía en veto.
3. **Pendiente/cuestión abierta**: el sentiment NO se invierte por dirección en el composite
   general (`_merge_and_rank` suma `sentiment_score_normalized` igual para largos y cortos) — R8
   es un parche de veto binario, no una corrección de raíz del scoring. Revisar si conviene.

Detalle completo y motivación en `docs/estrategia_riesgo.md` §3 (R8) y cabecera. Tests:
`tests/test_earnings_parsing.py`, ampliación de `tests/test_risk_policy.py`.

## 2026-07-30 (cont.) — Bug de splits sin ajustar en Alpaca (NO revertir)

Al re-correr el pipeline tras el fix de R8, contrasté los candidatos contra gráficos de
TradingView y encontré un bug de datos más grave que el de BE: `data/market_data.py` pedía
barras a Alpaca (`StockBarsRequest`) **sin `adjustment`**, que por defecto es `raw` (sin
ajustar por split). CRWD hizo split 4:1 el 2026-07-02 y su serie histórica quedaba con un
precipicio de precio (cierre $772,62 el 01/07 → $193,73 el 02/07) — corrompía `high_52w`
(785,59 en vez de 217,50 real), EMAs, Bollinger, soporte/resistencia, todo. El "downtrend
brutal" que el TA le puso a CRWD ese día era en gran parte artefacto del split.

**Alcance:** cualquier ticker de la watchlist con un split reciente (ventana de hasta ~6 años
para el MA200 semanal/diario) tiene el mismo problema — sesga sistemáticamente hacia
"downtrend" en el lado post-split.

**Fix:** `adjustment=Adjustment.ALL` añadido a los dos `StockBarsRequest` de `market_data.py`
(`_fetch_ohlcv_alpaca` y `_fetch_batch_quotes_alpaca`). Verificado en vivo: high_52w de CRWD
pasa a 217,355, coincide con TradingView. Detalle en `docs/estrategia_riesgo.md` §9.
