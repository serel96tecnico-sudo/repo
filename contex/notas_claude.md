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

## ⚠️ REGLA CRITICA (corregida 2 veces, NO volver a romperla) — columna "Total G/P€" de broker_1 (DeGiro)

La columna **"Total G/P€"** de la tabla de posiciones de DeGiro **NO es el resultado del trade/lote
actualmente abierto**. Es el acumulado historico de TODOS los trades que se han hecho alguna vez con
ese ticker en esa cuenta (incluye rondas ya cerradas hace meses, sumadas al lote actual). Ejemplo:
NVDA muestra "Total G/P€" > €1.350 porque suma la ronda cerrada el 06/08 (+€69,38) mas todo el
historico previo del ticker, no solo la posicion reabierta el 11/08.

**La columna que SI representa el trade actual es "G/P Potencial €"** (`gp_potencial_eur` en
`portfolio.json`) frente al BEP de esa entrada concreta.

**Consecuencia practica**: al cerrar una posicion, el `net_pl_eur`/`net_pl_usd` que se escribe en
`trades_historico.json` tiene que salir del extracto de transacciones real (compra vs venta
confirmadas) o, si no hay extracto todavia, de "G/P Potencial €" -- **NUNCA** de "Total G/P€". Usar
"Total G/P€" para eso falsearia el track record (arrastraria ganancias/perdidas de trades antiguos
ya registrados, duplicandolos). Se puede seguir registrando "Total G/P€" en `portfolio.json` como
dato informativo de fondo (asi esta ahora), pero etiquetado siempre como acumulado historico, nunca
como el resultado del trade en curso.

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

## 2026-08-04 — R9 nueva: calendario económico + guarda de evento macro de alto impacto

El operador pidió "conectar el calendario personal con un calendario económico" para
tener en cuenta eventos macro (catalizador o cisne negro) al operar. Dos piezas:

1. **`data/economic_calendar.py`** (nuevo) — descarga el feed público de Forex Factory
   (`nfs.faireconomy.media/ff_calendar_thisweek.json`, sin API key; busqué en GitHub un
   script existente para esto — los que hay son o manuales (fxstreet CSV) o requieren
   Selenium; este feed JSON directo es el que usan la mayoría de bots/indicadores y no
   necesita browser). Cachea en disco (`contex/economic_calendar_cache.json`, TTL 6h)
   porque FF limita a 2 descargas/5min del fichero semanal.
2. **R9** (`utils/risk_policy.py:macro_event_guard` + `orchestrator._apply_macro_event_guard`) —
   si hay un evento `impact=High` de `MACRO_EVENT_COUNTRIES` (USD por defecto) programado
   HOY, cualquier BUY/STRONG BUY/SELL/STRONG SELL nuevo se degrada a WATCH (ambas
   direcciones, a diferencia de R4/R8). No toca la cartera existente. Fail-open si el
   feed falla. Aplicado después de R7, antes de R1/R2/R6.

**Google Calendar.** Además, sincronicé (una vez, manual) el evento de alto impacto de
esta semana (NFP del 07/08, 08:30 ET) al calendario personal del operador
(`serel96tecnico@gmail.com`) vía el conector MCP de Google Calendar — esto es una acción
de Claude en la sesión, NO código Python del pipeline (el pipeline no tiene credenciales
de Google, ni las necesita para R9). Pendiente decidir con el operador si se quiere
automatizar con una tarea programada semanal (repetir esta sincronización cada semana) o
se hace a mano/pidiéndolo en sesión.

Detalle completo, motivación y cautelas en `docs/estrategia_riesgo.md` §3 (R9) y cabecera.
Tests: `tests/test_economic_calendar.py`, `tests/test_macro_event_guard.py`.

## 2026-08-04 — Sistema de alertas de pullback armadas (Telegram), no documentado hasta ahora

No estaba en `CLAUDE.md` ni aquí. Es una capa de **seguimiento** aparte del pipeline diario: cuando
un candidato queda en WATCH con una condición de entrada pendiente (ej. "esperar retroceso limpio
al EMA9"), este sistema lo vigila y avisa por Telegram en el momento en que el precio cumple esa
condición — para no perderse el punto de entrada de un setup que el pipeline ya propuso pero que no
era accionable ese mismo día. Piezas:

- **`scripts/arm_alerts.py`** — "arma" el trigger de pullback (`pullback_to_ema9`) de un candidato
  con el score y ATR del día en que se propuso (ej. ILF armada 31/07, score 6.62).
- **`scripts/price_watcher.py`** — vigila el precio y dispara la alerta por Telegram cuando toca
  el nivel armado. El mensaje incluye stop/T1 **recalculados** al precio del disparo (no los del
  momento del armado) y un aviso explícito: *"Falta antes de entrar: tamaño (R3/VIX) y validar
  R1/R2/R6/R7 contra la cartera actual. El stop guardado en el armado NO sirve a este precio."*
- **`agents/portfolio_watchdog.py`** — vigilancia de posiciones ya abiertas (riesgo de earnings
  próximos, etc.), sistema distinto al del seguimiento de setups.

**Importante:** la validación R1/R2/R6/R7 que pide el propio mensaje de la alerta es **manual, no
automática** — ninguno de estos scripts vuelve a correr `orchestrator._apply_exposure_caps`; el
diseño delega esa comprobación al operador en el momento de la entrada, no la bloquea. Caso real
04/08: alerta de ILF disparada y operada (22 @ $35.14, SL $34.21, TP $38.30), pero el informe diario
del pipeline de ese mismo día tenía ILF en WATCH bloqueada por el cap R1 de alta beta (80%
superado) con el mismo score (6.62) — la alerta avisaba de validarlo, no consta si se hizo antes de
entrar.

## 2026-08-06 — RESUELTO: BEP de TSM vs precio de ejecucion (403.67 es el STOP, no el BEP)

Cierre del 06/08 (broker_1): TSM abierta hoy (2 acciones). La tabla de posiciones mostraba
**$403.67** junto al BEP, pero el extracto de transacciones de DeGiro confirmo la compra a
**$413.28** (2 @, valor -EUR716.26, autoFX -EUR1.79, comision -EUR2.00, coste total EUR720.05).
**Confirmado por el operador el mismo dia: $403.67 es el STOP LOSS**, no el BEP -- la lectura
inicial de la tabla de posiciones que emparejaba ambos numeros era enganosa. BEP real = $413.28.
Consecuencia real: el stop ($403.67) queda **por debajo del coste** ($413.28), exponiendo
~$9.61/accion (~EUR16.65 en 2 acciones) de riesgo abierto -- NO es un stop a breakeven como
parecia. Ver `contex/portfolio.json` (acciones/TSM, campo `orden_pendiente` actualizado con la
advertencia) y `trades_historico.json`. Leccion para el futuro: en la tabla de posiciones de
DeGiro, comprobar si un numero que "coincide" con el stop es realmente el BEP antes de asumir
que la posicion esta protegida -- verificar contra el extracto de transacciones cuando haya duda.

## 2026-08-07 — Cierre del día (viernes) y cierre semanal

Sin cierres/aperturas nuevas en ningún broker (verificado: "Hoy no tiene transacciones" en
DeGiro, 3 posiciones sin cambios en Colmex) — solo mark-to-market + 2 stops trailados en
broker_2 (CRWD $196.00→$201.33, DXCM $78.12→$78.39). `trades_historico.json` NO se tocó
(nada que añadir). `portfolio.json` actualizado: `last_updated`, `cambios` (día + resumen
semanal 03/08-07/08), balances de ambos brokers y cada posición/ETF.

**Resumen semanal (03/08-07/08):** 5 cierres — PANW (+$4.55), BRKR (-$58.40, gap ~20% con
slippage), ANF (+EUR48.96), BAC (+EUR5.35), NVDA (+EUR69.38); realizado broker_1 +EUR123.69,
broker_2 -$53.85. 5 aperturas — CRWD, ILF (vía alerta de pullback), GLDA, PSX (reentrada),
TSM. broker_1 total B/P mejoró EUR-838.53→EUR-720.64 (+EUR117.89), con una retirada de
caja -EUR400 (04/08, no ligada a trading) que enmascara la mejora en `cuenta_completa`.
broker_2 balance sin cambios en la semana ($2,476.46).

**Pendiente recurrente (RESUELTO 2026-08-11, parcialmente):** GLDA llevaba sin stop-loss desde
su apertura el 05/08. Calculado el 11/08 con GLD (SPDR Gold Shares) como proxy de volatilidad
(GLDA no tiene datos en Alpaca ni Yahoo Finance) — ATR14 de GLD ≈1.86% del precio, 2.5×ATR
(mismo `ATR_STOP_MULTIPLIER` de `config.py`) daría ≈€139.54 desde BEP o ≈€144.05 trailing desde
precio. El operador eligió en su lugar un **stop a breakeven exacto (€146.35, el BEP)** —
prioriza proteger el capital ya en verde (+3.22%) sobre dejar más margen al ruido normal del
oro. Registrado en `portfolio.json` (`stop_loss: 146.35`). **Sigue pendiente**: la orden real
de venta stop en DeGiro — el pipeline no tiene API de órdenes hacia broker_1, así que el
operador debe introducirla manualmente. Verificar en el próximo cierre que ya esté colocada.

## 2026-08-14 — Cierre del dia (viernes) y cierre semanal: FTNT posiblemente por debajo de su stop sin ejecutar

Cierre 10/08-14/08. Semana positiva: broker_1 total B/P mejora +€37.53 (-€720.64→-€683.11) pese al
dia rojo de hoy; broker_2 balance sube +$127.48 ($2,476.46→$2,603.94). Cerradas la semana: PSX
(+€37.32), CRWD (+$110.02), DXCM (+$55.90), ILF (-$17.24), TSLA (-€15.07), AME (+€36.65). Sin
trades cerrados hoy en ningun broker (portfolio.json actualizado solo con mark-to-market).

**Pendiente urgente**: FTNT (broker_1) cotiza $159.955, por debajo del stop confirmado el 12/08
($160.14), pero 0 transacciones hoy -- la venta no ha ejecutado. La captura de 'Ordenes pendientes'
de hoy muestra la linea de FTNT truncada a '$159' (sin decimales, a diferencia de las demas lineas
que si los muestran), asi que no se puede confirmar si el stop sigue en $160.14 o se ha modificado/
cancelado. Verificar con el operador antes de la apertura del lunes. **Patron a recordar**: cuando
el panel de ordenes pendientes de DeGiro trunque un precio sin decimales mientras las demas lineas
si los muestran, tratarlo como señal de posible orden distinta a la registrada, no solo un problema
de formato -- contrastar contra el stop conocido en vez de asumir que coincide.

**Nota positiva**: SLNH (broker_2) recupero su stop ($0.92) y TP ($2.90) tras el earnings del 13/08
AMC -- confirmado visualmente y por el conteo de 'Ordenes 6' en Colmex. Cierra el pendiente abierto
desde el 12/08 (stop quitado deliberadamente de cara al evento).

---

## 2026-08-18 — Cierre del día (martes): 4 cierres

broker_1 rotó 3 posiciones USD antiguas (TSM, NVDA, SBET, todas vendidas 15:35-15:37) para abrir CVX
(Chevron, 4@$204.93, stop $196.30). broker_2 cerró SLNH (desapareció de la tabla de Posiciones).

- **SBET** (broker_1): inicialmente ESTIMADO +€146.96 (posición antigua sin coste de entrada en EUR
  confirmado); el operador aportó el extracto de transacciones DeGiro después → **CONFIRMADO
  +€141.72** (venta €651.35 TC 1.1579, compra €509.63 TC 1.1399). Corregido en `portfolio.json` y
  `trades_historico.json`.
- **SLNH** (broker_2): inicialmente ESTIMADO +$24.87 (vía delta de balance, sin histórico de órdenes);
  el operador aportó la captura del histórico de órdenes de Colmex después → **CONFIRMADO** ejecución
  200@$1.30, 15:38:33, net ~+$23.50 (fee -$2.50 asumido por patrón habitual, no desglosado en el
  histórico). Corregido en ambos ficheros.
- **TSM** (broker_1): el operador aportó también el extracto detallado (con TC/autoFX/comisiones) de
  las 4 transacciones de hoy → **CONFIRMADO -€2.21** (venta €717.84 TC 1.1581, compra €720.05).
- **NVDA** (broker_1): el operador aportó también el extracto de la compra del 11/08 (reentrada) →
  **CONFIRMADO -€3.91** (venta €568.93 TC 1.1581, compra €572.84 TC 1.1544). Los 4 trades del cierre
  del 18/08 quedan así completamente confirmados por extracto, sin estimaciones pendientes.

**Patrón a recordar**: cuando falte el desglose de una venta/cierre en la captura inicial, marcar
ESTIMADO y seguir adelante — si el operador aporta después el extracto real (transacciones DeGiro,
histórico de órdenes Colmex), sustituir el número y dejar constancia de la corrección, no solo del
valor final.

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

## 2026-08-09 — Reconciliación completa de `trades_historico.json` contra extractos reales (NO revertir sin re-auditar)

El operador pidió el track record 2026 y, al contrastarlo con capturas puntuales de Colmex
(AAPU, LMND), aparecieron errores de comisión. Eso escaló a una reconciliación completa de
**ambos brokers** contra sus fuentes primarias:

- **broker_2 (Colmex)**: `tradeHistory_COLH50450.csv` (histórico de ejecuciones, con
  `PositionId` para agrupar) + `pl-report_COLH50450.csv` (P/L diario oficial del broker,
  usado como **validación independiente** — el gross realizado y la comisión total cuadraron
  al céntimo: -$468.12 y -$477.62 respectivamente).
- **broker_1 (DeGiro)**: `Transactions.csv` (histórico de transacciones; sin `PositionId`,
  reconstruido agrupando por ISIN/producto con cantidad acumulada — un bloque se cierra
  cuando la cantidad neta vuelve a 0). Sin reporte de P/L diario propio para verificación
  cruzada como en Colmex, pero la comparación ticker-por-ticker (suma neta agregada, no por
  lote individual) cuadró exactamente en todos los tickers salvo los que cruzan a 2025 (BTC,
  JMIA, DLO, NFLX — ya registrados correctamente con su pata de 2025 fuera del extracto).

**Errores encontrados (patrones, no casos aislados):**
1. **Comisión mal contada** — el patrón más frecuente: se restaba una sola comisión (-$2.50)
   cuando la operación tuvo 2 o 3 ejecuciones (compra+venta, o 2 compras+venta) a $2.50 cada
   una. Afectó a más de una decena de trades en broker_2.
2. **Precio de cierre incorrecto en 2 casos** (WULF y NVO, ambos 09/06 broker_2): se había
   registrado el **TP objetivo planeado** como si fuera el precio de ejecución real, en vez
   de esperar la confirmación del fill. WULF pasó de +$79 (ficticio) a +$15.50 (real); NVO de
   +$55 a -$0.40.
3. **Operaciones enteras nunca registradas** — el problema mayor en volumen: **33 en
   broker_2** y **23 en broker_1** (56 en total) faltaban por completo del histórico, muchas
   perdedoras grandes (SMR -$156.70, INTC -$117.68/-$178.08€, IONQ -€230.65, RXT -$101.00,
   QBTS -$92.00 en broker_2; el ticker INTC no existía siquiera en broker_1 antes de esto).

**Resultado (2026 completo, 179 trades cerrados, antes 121):**

| | Antes de reconciliar | Después (real) |
|---|---|---|
| broker_1 (EUR) | -262,78 € (67 trades) | **-655,02 €** (90 trades) |
| broker_2 (USD) | +45,18 $ (54 trades) | **-945,74 $** (89 trades) |
| Winrate combinado | ~52% (sesgado por huecos) | **46,9%** |

La imagen de rentabilidad que daba el histórico sin depurar era bastante más optimista que la
real, sobre todo por los trades perdedores nunca registrados. Junio y julio 2026 son, con
diferencia, los peores meses del año (~-1.470€/$ combinados) — antes casi invisibles en el
histórico.

**Lección para el workflow "cierre del día"**: los netos marcados como *ESTIMADO* (ver sección
de arriba) deben tratarse con más recelo — la acumulación de pequeños redondeos y comisiones
mal contadas en estimaciones manuales es lo que produjo este desfase de ~1.600€/$ a lo largo
del año. Cuando se pueda, confirmar contra el extracto real (`tradeHistory_*.csv` en Colmex,
`Transactions.csv` en DeGiro) en vez de dejar la estimación sin revisar. Sería razonable
repetir esta reconciliación completa cada 1-2 meses en vez de esperar a que se acumule tanto.

## 2026-08-12 — Caso FROG: "contradicción" pipeline vs indicadores TradingView es desajuste de timeframe

El operador notó que los indicadores de su chart de TradingView (FROG, NASDAQ, 1h: **SSL
Channel** de MissTricky con canal SMA-high/low 200, y **NSDT HAMA Candles** con velas
sintéticas EMA20-25 + gradiente de fuerza) parecían "contradecir" el report del pipeline del
2026-08-11 evening (FROG WATCH score 7.6, LARGO, bloqueado por cap R1 de alta beta, EMA9/21/50
diarios en stack alcista, pide pullback a ~$85.29 con volumen y vela de confirmación).

**No es contradicción de tesis, es desajuste de timeframe/exigencia**: el pipeline lee
**diario** (medias lentas, aún no reflejan el rollover intradía tras el gap de earnings del
06/08); los indicadores del chart leen **1h**, mucho más reactivos, y ya mostraban el HAMA
rompiendo su ribbon (87.68-88.20) con racha bajista acelerándose, mientras el SSL(200,1h)
todavía no daba señal de venta (a solo ~$1.90 de darla). El pipeline ya pedía exactamente ese
pullback como condición de entrada — el desacuerdo real está en el **carácter** del movimiento:
el pipeline exige una pausa ordenada con vela alcista de confirmación y volumen, y lo que se
veía en el intradía lucía más a ruptura sin rebote que a pausa. FROG es candidato en
`portfolio.json` desde el 04/08 (no posición abierta), condición pendiente:
"Pullback a EMA9/EMA21 con vela alcista de confirmación".

**Patrón a recordar**: cuando el operador compare una lectura del report (diario) con su chart
de TradingView en timeframe intradía, comprobar primero si son literalmente el mismo horizonte
antes de asumir que hay contradicción — casi siempre el intradía se adelanta al diario, no lo
contradice, salvo que el nivel diario clave (aquí EMA9 ~85.29) llegue a romperse también.

## 2026-08-19 — FROG cerrada por stop en el wick de apertura; R9 (FOMC) confirma que aún nadie estaba activable

Report de la mañana (15:50 CEST) con los 4 candidatos en WATCH, ninguno accionable: OSCR/AEHR
bloqueados por R9 (Actas del FOMC de hoy), MSFT/FTNT por debajo del umbral de score. El operador
preguntó por la volatilidad del día y se revisaron los 4 setups en vivo vía TradingView MCP
(`chart_set_symbol` + `quote_get` + `data_get_ohlcv`): ninguno había recuperado el nivel de fuerza
que exige R5 para activarse — OSCR se acercaba ligeramente, AEHR y FTNT se alejaban más (AEHR con
una caída fuerte de -16.5% en 3 sesiones, incluyendo la de hoy), MSFT plano. **Aclaración de
timing**: las Actas del FOMC se publican normalmente ~14:00 ET / 20:00 CEST; a media mañana (hora
de la revisión) todavía no habían salido — la volatilidad vista hasta ese momento era apertura de
sesión normal + dinámica propia de AEHR, no el evento en sí.

Al revisar también las posiciones abiertas (no las del report, pero relevantes por la volatilidad),
**FROG (broker_2) hizo un wick hasta $87.39 en velas de 15min hacia las 14:00 UTC** (justo la
apertura), por debajo de su stop trailing ($89.31, puesto el 17/08 por encima del BEP $87.67), y se
recuperó a ~$89.7 minutos después. El operador confirmó por captura del histórico de órdenes Colmex
que el stop **sí ejecutó**: 9 acciones vendidas a $89.09 medio, 15:37:06, net +$10.30 (gross
+$12.80). Registrado en `portfolio.json` (quitada de `acciones`, añadida a `cerradas_semana`) y en
`trades_historico.json`. **Pendiente**: `balance_usd`/`open_net_pl_usd`/margin de broker_2 en
`portfolio.json` siguen siendo los del 18/08 — refrescar con captura nueva de Colmex en el próximo
cierre del día.

**Patrón a recordar**: un wick intradía que perfora un stop y se recupera en minutos NO significa
que el stop no saltara — si es una orden stop real en el broker (no solo mental), casi seguro
ejecutó en el toque aunque el precio ya esté de vuelta por encima al mirar el chart. Verificar
siempre contra el histórico de órdenes del broker, no contra el precio "actual".

## 2026-09-02 — Informe duplicado por Telegram: dos programadores corriendo a la vez

El pipeline se lanzaba **dos veces** a las mismas horas (15:40 y 20:30), cada una mandando su
propio informe a Telegram vía `orchestrator.py:191`. Causa: convivían dos mecanismos de
programación independientes, ambos activos:
1. Servicio de Windows NSSM `TradingAgent` ("Trading Agent (Saetabia)") corriendo
   `main.py --schedule` → `scheduler.py` (usa `RUN_TIME`/`RUN_TIME_EVENING` de `config.py`).
   Es el camino de producción real (así lo documentan los comentarios de `scheduler.py` y
   `draw_levels.py`).
2. Tareas de Task Scheduler `SwingTradingAgent`/`SwingTradingAgentEvening`, reinstaladas el
   27/08 (ver entrada de esa fecha) con `--setup-scheduler` porque parecían faltar — sin saber
   que el servicio NSSM ya cubría lo mismo. Desde el 27/08 hasta hoy corrieron en paralelo.

**Fix aplicado**: borradas las tareas `SwingTradingAgent` y `SwingTradingAgentEvening` de Task
Scheduler (`schtasks /delete /tn ... /f`). Se deja el servicio NSSM `TradingAgent` como único
disparador. `SwingTradingAgentSupportScan` NO se toca — `scheduler.py` no dispara
`--support-scan`, así que esa tarea es independiente y no duplica nada.

**Si vuelve a faltar el informe** (como el 27/08): comprobar primero `Get-Service TradingAgent`
(debe estar `Running`, arranque `Automatic`) antes que `Get-ScheduledTask` — el servicio NSSM es
ahora el único camino. **No volver a ejecutar `python main.py --setup-scheduler`** salvo que se
elimine también el servicio NSSM antes, o se volverá a duplicar.

## 2026-09-02 — CRWD (broker_1) cerrada por stop con slippage + bug descubierto: `--close-trade` escribe en el fichero equivocado

**CRWD stop saltado**: stop confirmado en $208.40 (01/09), ejecutado real -3@$204.43 (16:02) —
$3.97/acción peor, ~$11.91 de slippage extra en las 3 acciones. Gross -$26.46, net EUR
**ESTIMADO** -€24.81 (TC~1.16 + fees típicos ~€2, sin extracto DeGiro todavía — confirmar en el
próximo cierre). Registrado en `portfolio.json` (`acciones` sin CRWD, `cerradas_semana[0]`) y
`trades_historico.json` (append). broker_1 queda sin CRWD, resto de posiciones sin tocar.

**Bug descubierto, no corregido todavía**: `python main.py --close-trade` (`PortfolioTracker` en
`agents/portfolio_tracker.py` + `context_manager.py:update_trade_history`) escribe en
**`contex/trade_history.json`** (singular) — un fichero huérfano que **NINGÚN** otro consumidor
lee. El flujo real ("cierre del día", este mismo fichero de notas, y lo que está bajo control de
versiones activo) usa **`contex/trades_historico.json`** (plural) vía edición manual o
`scripts/registrar_cierre.py`/`utils/telegram_bot.py`. Probé `--close-trade` para este cierre,
vi que no tocaba `trades_historico.json`, y **revertí** la entrada que dejó en
`trade_history.json` para no tener el dato duplicado con esquemas distintos en dos sitios.
`scripts/generate_performance_report.py` añade más confusión: su cabecera dice que
`trades_historico.json` "quedó congelado el 2026-06-10" y que el log vivo es
`portfolio.json['cerradas_semana']` — eso sí es cierto (confirmado: `R7 recent_loss_cooldown`
en `utils/risk_policy.py` lee de ahí, no de ningún trade_history), pero la afirmación de que
`trades_historico.json` está congelado es **falsa**: se ha seguido escribiendo a mano
continuamente (última entrada antes de hoy: 31/08). Tres ficheros con función solapada
(`trade_history.json`, `trades_historico.json`, `portfolio.json.cerradas_semana`), documentación
desalineada.

**RESUELTO el mismo día (2026-09-02)**: antes de borrar `trade_history.json` comparé sus 11
trades (may-2026) contra `trades_historico.json` — 10 ya estaban duplicados (2 con datos algo
distintos porque `trades_historico.json` los tiene vía backfill del extracto real de
DeGiro/Colmex, más fiables); 1 (**ALRIB**, Riber SA, cerrada 12/05 -€38.00) no existía en
ningún otro sitio y lo fusioné a mano antes de borrar el fichero huérfano. `agents/portfolio_tracker.py`
recableado: `history_path` → `trades_historico.json`, `log_closed_trade()` y `_build_report()`
reescritos para el esquema real (`entrada_*`/`cierre_*`/`fecha_cierre`/`nota`, sin `resultado`
explícito — win/loss se infiere del signo del neto). Mismo fix en el código muerto
`context_manager.py:update_trade_history()` (nadie lo llama, pero tenía el mismo bug de ruta +
guardaba una lista plana en vez de `{"trades": [...]}`). Verificado: `--portfolio-report` ahora
muestra el histórico completo (198 trades, ene-sep 2026) en vez de las 11 del fichero huérfano;
159/159 tests siguen pasando. `--close-trade` ya es seguro de usar de nuevo.
