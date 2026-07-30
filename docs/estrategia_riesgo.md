# Especificación de Estrategia y Riesgo

> **Estado:** v1 — 2026-06-13. Primera spec de estrategia definida explícitamente por el operador (no heredada de defaults del modelo).
> **Implementación:** R1/R2/R3 cableadas en código el 2026-06-15 (`config.py` §4 + `utils/risk_policy.py`, aplicadas en `risk_manager.py` y `orchestrator._apply_exposure_caps`). R4 ya vivía en el orchestrator. R5 (calidad de entrada, calibrada con `backtest_entry_quality.py`) en `risk_manager.py`. **2026-06-24:** corrección estructural — `PORTFOLIO_VALUE` al capital real (€7.000), **R3 redefinida** como perfil de riesgo dinámico por trade (1–3 % inverso al VIX, reemplaza el half-size fijo) y **R6 nueva** (tope de riesgo agregado de cartera al 10 %). **Fix régimen:** `regime` se derivaba solo del VIX y podía decir BULLISH con el SPY en Downtrend, bloqueando cortos en las caídas; ahora `classify_regime` lo hace coherente (dirección por precio, matizada por VIX) y el gate de dirección de R4 lee `spy_trend`, no la etiqueta de volatilidad. Tests en `tests/test_risk_policy.py` y `tests/test_entry_quality.py`.
> **Ámbito:** política de exposición, concentración y sizing. NO toca los indicadores técnicos del pipeline.
>
> **2026-07-30:** **R8 nueva** (guarda de catalizador de sentiment alcista en cortos, caso BE) en `utils/risk_policy.py` (`short_bullish_catalyst_guard`), aplicada en `orchestrator._merge_and_rank`. Fix de bug relacionado en `FundamentalAnalyst._parse_earnings_days` (earnings de ayer se calculaba como "364 días", desactivando de facto cualquier lectura de proximidad de earnings). Tests en `tests/test_earnings_parsing.py` y ampliación de `tests/test_risk_policy.py`.

---

## 0. Por qué existe este documento

El sistema se construyó pidiendo *"un sistema para aumentar capital"* sin definir estrategia. El modelo rellenó el hueco con indicadores convencionales (RSI, MACD, EMA, ATR…) y un sesgo de **momentum de alta beta** que nadie eligió conscientemente.

El análisis de las primeras ~30 operaciones (15 may – 10 jun 2026) reveló que **el problema no eran los indicadores ni "mal mercado"** (el SPY estaba en *Uptrend* durante la sangría), sino **concentración de factor**: el 5-jun había ~6 longs del mismo tema (quantum + mineros) abiertos a la vez y cayeron todos juntos → ≈ −495 en un día. El resto del histórico era neto positivo (≈ +376, win rate 57%, cortos 6/7 ganadores).

**Conclusión:** falta una capa de riesgo/concentración. Este documento la define.

---

## 1. Tesis explícita

> Operamos **momentum de growth/alta beta** porque es donde está el movimiento este semestre y donde se generan los grandes aciertos (CLSK +213, IONQ +134, APP +140). **Aceptamos la varianza** que eso implica, pero la acotamos con un tope de exposición dinámico y reglas de concentración, para que un giro de factor no se lleve el libro entero por delante.

Lo que esto **NO** es:
- No es predecir el mercado. Es gestionar exposición a un factor que ya está corriendo.
- No es garantía de rentabilidad. Es reducir el daño de los eventos correlacionados.
- No cambia las señales técnicas; cambia **cuánto** y **cuántos** a la vez.

---

## 2. Clasificación de activos (tiers de beta)

| Tier | Descripción | Ejemplos del histórico |
|---|---|---|
| **A — Núcleo** | Large-caps establecidos, beta baja/media | AAPL, MSFT, GOOGL, NVO, CRM, NOW, COST, PYPL, BMW, NKE |
| **B — Growth alta beta** | Large/mid-cap volátiles pero líquidos | AMD, NVDA, TSLA, APP, MRVL, COIN |
| **C — Especulativo / micro-tema** | Quantum, mineros cripto, meme, pre-beneficios | RGTI, QBTS, IONQ, WULF, CLSK, IREN, CIFR, ONDS, OPEN, GRAB, OSCR, ASTS, SMR, RKLB, LUNR |

- **"Alta beta"** a efectos del cap = **Tier B + Tier C**.
- La sangría del 5-jun fue **íntegramente Tier C**. Por eso Tier C lleva controles extra (sub-temas y half-size).

**Sub-temas dentro de Tier C** (para la regla de concentración): `quantum`, `mineros_cripto`, `meme_spec`, `space_nuclear`, `otros`.

---

## 3. Reglas

### R1 — Cap de exposición a alta beta, dinámico por régimen
Decisión del operador: mantener exposición alta a growth pero **flexionarla con el mercado** (no conteo rígido). El % es sobre el capital invertido en posiciones, no sobre el cash.

| Régimen registrado | VIX | Cap máx. alta beta (B+C) | 
|---|---|---|
| Strong Uptrend | < 18 | **80 %** |
| Uptrend | 18 – 22 | **70 %** |
| NEUTRAL / Sideways | ≥ 20 (elevado) | **60 %** |
| Downtrend / risk-off | > 25 | **40 %** |

> El resto hasta 100 % se cubre con Tier A (núcleo) o cash. El cap se revisa y ajusta según cuánto favorezca el mercado al growth (ver §5).

### R2 — Concentración por sub-tema (la lección del 5-jun)
Ningún **sub-tema** de Tier C puede superar el **40 % del bucket de alta beta**.

> En la práctica esto significa ≈ **no más de 2–3 nombres del mismo sub-tema** (p. ej. quantum) abiertos a la vez. Es una guía de diversificación dentro del factor, no un rechazo rígido: si quantum corre, participas — pero no con todo.

### R3 — Perfil de riesgo dinámico por trade (redefinida 2026-06-24)
Decisión del operador: el riesgo por operación no es fijo, **flexiona inverso al VIX** — más riesgo en calma (es cuando el momentum funciona, lo confirma el backtest de R5), menos en estrés (los breakouts fallan y los stops saltan en cascada). Reemplaza al antiguo half-size fijo.

| Régimen | VIX | Riesgo/trade | € sobre 7.000 |
|---|---|---|---|
| Strong Uptrend | < 18 | **3.0 %** | €210 |
| Uptrend | 18 – 22 | **2.0 %** | €140 |
| NEUTRAL / Sideways | ≥ 20 | **1.5 %** | €105 |
| Downtrend / risk-off | > 25 | **1.0 %** | €70 |

> `shares = (RISK_PCT × PORTFOLIO_VALUE) / (entrada − stop)`, **acotado por la banda de inversión bruta €500–800** y por `MAX_POSITION_PCT` (techo absoluto de capital). Misma lectura defensiva de régimen que R1 (VIX≥20 fuerza el bucket NEUTRAL aunque el SPY siga en Strong Uptrend).
>
> **Banda de inversión bruta (€500–800).** El motor de riesgo propone el tamaño y se acota a la banda. Decisión del operador: **la inversión manda a nivel de trade** — el suelo de €500 se respeta con acciones enteras aunque eleve el riesgo por encima del % del régimen (ej.: acción de €400 → 1 acc = €400 < €500 → **2 acc = €800**). El control de riesgo que NO se relaja es el agregado: **R6 (10 % de cartera) sigue siendo el límite duro**. Lógica pura en `size_position()` (testeable).
>
> *Por qué el suelo de €500:* la comisión es **fija** por operación (`BROKER2_COMMISSION = $5` ida+vuelta), independiente del valor del activo. Por debajo de €500 ese coste fijo se vuelve un lastre desproporcionado — €5 sobre €250 = 2 % solo en comisiones, sobre €500 = 1 %, sobre €800 = 0,6 %. Con win rate ~43 % y rentabilidad concentrada en pocos ganadores (§7), no se pueden regalar 1-2 % de breakeven a la comisión: el suelo mantiene el drag en ≤1 %.

### R6 — Tope de riesgo agregado de cartera (nueva 2026-06-24)
La suma del **riesgo abierto** (posiciones existentes + nuevas) no puede superar el **10 % del capital** (€700 sobre 7.000). Si una nueva recomendación lo rompería, se degrada a WATCH (igual que R1/R2).

> Este es el verdadero freno al clúster correlacionado. La lección del 5-jun es que **VIX bajo NO protege**: aquella sangría ocurrió con el SPY en *Uptrend*. El tope agregado se autorregula con R3 — en calma (3 %/trade) caben ~3 posiciones antes del tope; en estrés (1 %/trade) caben más y más pequeñas. Pase lo que pase, un día negro no se lleva más de ~10 % del libro.
>
> **Riesgo de una posición** = `(precio − stop) × acciones` (longs) / `(stop − precio) × acciones` (cortos). Si una posición **no tiene stop colocado**, se asume un stop por defecto a `DEFAULT_STOP_PCT` (8 %) — opción del operador: contar el riesgo real, no ignorarlo. Una posición en verde con el stop ya sobre el precio aporta riesgo 0.

### R7 — Enfriamiento de re-entrada tras pérdida reciente (nueva 2026-07-14)
Si un ticker **cerró en pérdida** (neto > `RECENT_LOSS_MIN_ABS`, para no contar scratch) en los últimos **`RECENT_LOSS_COOLDOWN_DAYS` días** de calendario, no se re-recomienda en la **misma dirección**: queda WATCH-only. Se aplica en `orchestrator._apply_recent_loss_cooldown` (antes de R1/R2/R6), con la lista de cierres de `portfolio.json['cerradas_semana']` vía `recent_loss_cooldown()`.

> **Motivación (análisis de selección jul-2026).** Sobre 3 semanas de recomendaciones, el motor re-recomendaba una y otra vez nombres que acababan de stopear —GRAB ×3, MRVL, INTC, todos perdedores— sin memoria de que el setup acababa de fallar. El `composite_score` **no** discriminaba ganadores de perdedores (7.01 vs 6.90 de media), así que el problema no era el ranking sino la ausencia de un veto de re-entrada. Dar al nombre unos días para "resetear" ataca ese patrón directamente.
>
> **Dirección.** Un largo perdedor veta nuevos **largos** de ese ticker; un corto perdedor veta nuevos **cortos**. Un cierre sin dirección registrada (registros antiguos con `direccion: "?"`) veta **cualquier** dirección (conservador).
>
> **Cautelas (§7).** Muestra pequeña (13 trades resueltos) y nombres repetidos que pesan mucho (MRVL/GRAB). Es un **prototipo**: la ventana (5 días) y el umbral (€10) son la primera aproximación del operador, pendientes de calibrar con más datos. No usa aún el precio forward — solo el hecho del cierre perdedor.

### R8 — Guarda de catalizador de sentiment alcista en cortos (nueva 2026-07-30)
Si el `NewsSentimentAnalyst` encuentra un catalizador reciente (`catalyst_found=True`) y el `sentiment_score_normalized` supera `SHORT_BULLISH_CATALYST_MIN` (7.5), el corto se degrada a WATCH sin más cálculo de score — mismo tratamiento duro que el resto de R4. Implementado en `short_bullish_catalyst_guard()` (`utils/risk_policy.py`), aplicado en `orchestrator._merge_and_rank` antes que los demás guards de R4.

> **Motivación (caso BE, 29/07/2026).** Se abrió un corto un día después de que Bloom Energy publicara un earnings-beat con guidance al alza (récord de ingresos, backlog $20B). El sentiment analyst **sí** detectó el catalizador (`sentiment_score_normalized=8.9`, `catalyst_found=True`) y el propio resumen del risk_manager avisaba en texto de "riesgo clave: short squeeze impulsado por el catalizador positivo de earnings" — pero nada convertía ese aviso en un veto. El squeeze posterior (+25% en 24h) forzó el stop con fuerte slippage (net ≈ −$127 sobre una posición de 4 acciones). El sistema tenía la señal; le faltaba la regla.
>
> **Por qué el sentiment no se invierte para cortos en el composite general.** El `sentiment_score_normalized` se suma con el mismo peso positivo en `_merge_and_rank` sea cual sea la dirección (no hay lógica direccional en el composite) — R8 no arregla eso de raíz, solo actúa como veto binario cuando la combinación catalyst+sentiment es lo bastante fuerte. Revisar si el composite debería invertir el sentiment para cortos es una cuestión abierta (§6).
>
> **Relación con Filtro B.** Filtro B (earnings dentro de la ventana de hold, §8) es forward-looking: bloquea abrir/mantener una posición ANTES de un reporte. R8 cubre el hueco simétrico: el día(s) DESPUÉS del reporte, cuando el catalizador ya es público pero el mercado todavía puede seguir reaccionando (squeeze). Son complementarios, no redundantes.
>
> **Bug relacionado, corregido el mismo día.** `_parse_earnings_days()` (`agents/fundamental_analyst.py`) calculaba `earnings_days_away=364` para BE en vez de `-1` (earnings de ayer), por una comparación de fechas que asumía "año que viene" en cuanto la fecha parseada quedaba más de un día atrás de `datetime.now()`. Esto no causó directamente el fallo de R8 (que se basa en sentiment, no en `earnings_days_away`), pero sí es la causa raíz de que ninguna métrica del pipeline reflejara correctamente "esto acaba de reportar resultados". Fix: probar las 3 interpretaciones de año (anterior/actual/siguiente) y tomar la más cercana a hoy, sin heurística de umbral. Tests en `tests/test_earnings_parsing.py`.
>
> **Cautelas.** Umbral (7.5) es una primera aproximación, sin backtest — igual que R7 en su día. Solo actúa cuando `catalyst_found=True`; un sentiment alcista "de fondo" sin evento fresco identificado no dispara el guard (evita ser demasiado restrictivo con cortos en nombres que simplemente tienen buena prensa reciente).

### R4 — Cortos (sin cambios estructurales)
Los cortos realizados funcionaron (6/7 ganadores, +207). Se mantienen las reglas vigentes: solo Broker 2, gated por régimen (no abrir en *Strong Uptrend*), entrada en pullback a EMA9. Esta spec no los modifica.

### R5 — Calidad de entrada (exigir fuerza, no comprar debilidad)
Decisión del operador: *"si una operación se vuelve negativa nada más abrirla es un fracaso de estrategia; abrir en verde da margen para cerrar si te has equivocado."* El principio es correcto; la palanca la fijó el backtest.

**Calibración (backtest_entry_quality.py, 83 recs largas 06/05-15/06).** La primera hipótesis era "no perseguir extensión". El backtest la **falsó**: la extensión no causa el rojo inmediato — lo causa la **debilidad** (comprar con el precio flojo o por debajo de la EMA9).

| Entrada (fuerza = (precio−EMA9)/ATR) | abre rojo | % stop | ret +5d |
|---|---|---|---|
| **Débil** (≤0.5 ATR) | 58 % | 62 % | +1.0 % |
| **Con fuerza** (>0.5 ATR) | 27 % | 36 % | +8.1 % |

Y por tier en el subconjunto extendido (>1 ATR), el **breakout extendido de Tier C fue el mejor** (abre rojo 21 %, stop 7 %, +10.7 %) → no hay que frenarlo. Correlación extensión→retorno +1d = **+0.36** (positiva).

**Regla:** un largo es **débil** si cotiza < **0.5 ATR sobre la EMA9** (`WEAK_ENTRY_ATR_MIN`). En ese caso no se entra a mercado: la entrada es una **entrada-stop en el umbral de fuerza** (EMA9 + 0.5·ATR) — comprar solo cuando recupere fuerza, no en la caída. Con fuerza (≥0.5 ATR), entrada a mercado (los breakouts, incluidos los de Tier C, funcionan).

> Cautelas (§7): muestra pequeña y **un solo régimen** (may-jun, tendencial). En NEUTRAL/bajista los breakouts fallan más y esto podría girar; revisar con más datos. Evitar comprar debilidad es seguro en cualquier régimen (no compras cuchillos cayendo); el "entrar a mercado con fuerza" está validado en tendencia. Sesión de tarde (`evening`): entrada a mercado sin cambios.

#### R5b — Base-breakout ATR-relativo (calidad de SEÑAL, no de entrada) — añadido 2026-07-09
Decisión del operador: las señales de momentum "a pelo" (perseguir máximo de N días) generaban entradas que abrían en rojo, sobre todo en nombres hipervolátiles (caso CIFR, 2026-07-09: ATR ~10 % del precio → tras +10 % en 2 días, 72 % de caída intradía ≥2 %). Se buscó un setup de más calidad: **ruptura de canal/triángulo tras una consolidación tensa**.

**Metodología (3 backtests sobre 105 tickers, ~2,4 años, `backtest_entry_modes.py` → `_robustness.py` → `_base_breakout.py` → `_base_atr.py` → `_walkforward.py`).** Se probaron y **descartaron** dos ideas antes de llegar a la buena:
- *Pullback-limit en la EMA9 para ATR alto*: robusto a parámetros pero **se cayó fuera de muestra** (≥8 % ATR: +0.358 IS → +0.017 OOS). Descartado — era efecto de régimen.
- *Extension-guard* (no comprar tras subidón): las señales descartadas rendían **+0.157R** (buenas). Descartado — confirma que la extensión no es el problema (coherente con R5).

Lo que **sí** sobrevivió al walk-forward (IS→OOS), única señal que aguanta en nombres calientes:

| Señal en ≥8 % ATR | R/trade IS | R/trade OOS | win OOS |
|---|---|---|---|
| Momentum (máximo 10 sesiones) | +0.043 | +0.051 | 37 % |
| **Base-breakout (coil ≤5·ATR)** | **+0.500** | **+0.754** | **63 %** |

**Regla (capa de indicadores, no toca las reglas R de sizing/exposición):** `detect_base_breakout()` en `data/indicators.py` marca un base-breakout cuando, en tendencia (precio>EMA200 en pendiente +), el rango de los 20 días previos ≤ **5·ATR** (coil relativo a *su* volatilidad), el ATR está contraído, y hoy **cierra sobre el techo de la base** (ruptura fresca). `score_technical_setup` le suma **+2.0** (refuerzo moderado, elegido por el operador). Se expone también al prompt del analista técnico.

> Cautelas: en ≥8 % ATR la muestra es **fina (~29 trades, 10 IS + 19 OOS)** — es coherente en ambas mitades pero no concluyente; tratar como refuerzo, no certeza. Sólido en <3 % ATR (muestra grande, aguanta OOS). Un solo universo (watchlist actual → sesgo de superviviente) y un régimen macro (alcista cripto/IA). Revisar con más historia/universo antes de subir el peso o convertirlo en gating.

---

## 4. Parámetros (implementados en `config.py` desde 2026-06-15)

```
PORTFOLIO_VALUE           = dinámico   # broker_1.cuenta_completa_eur + broker_2.balance_usd
                                       # leído de portfolio.json cada run (USD~EUR a la par)

# Caps de exposición alta beta por régimen (R1)
HIGH_BETA_CAP_STRONG_UP   = 0.80
HIGH_BETA_CAP_UPTREND     = 0.70
HIGH_BETA_CAP_NEUTRAL     = 0.60
HIGH_BETA_CAP_RISKOFF     = 0.40

# Concentración por sub-tema dentro del bucket alta beta (R2)
SUBTHEME_MAX_PCT          = 0.40

# R3 — Perfil de riesgo dinámico por trade (inverso al VIX)
RISK_PCT_STRONG_UP        = 0.03
RISK_PCT_UPTREND          = 0.02
RISK_PCT_NEUTRAL          = 0.015
RISK_PCT_RISKOFF          = 0.01

# R3 — Banda de inversión bruta por trade (la inversión manda; suelo con acc. enteras)
MIN_INVEST_PER_TRADE      = 500
MAX_INVEST_PER_TRADE      = 800

# R6 — Tope de riesgo agregado de cartera + stop por defecto sin SL colocado
PORTFOLIO_RISK_CAP_PCT    = 0.10
DEFAULT_STOP_PCT          = 0.08

# R7 — Enfriamiento de re-entrada tras pérdida reciente
RECENT_LOSS_COOLDOWN_DAYS = 5
RECENT_LOSS_MIN_ABS       = 10.0

# R8 — Guarda de catalizador de sentiment alcista en cortos
SHORT_BULLISH_CATALYST_MIN = 7.5
```

> Estos valores viven aquí como **decisión** y están replicados en `config.py`. La clasificación de tiers (§2) y sub-temas vive en `utils/risk_policy.py` como **semilla editable**: los nombres no listados se clasifican por capitalización (>=100B→A, >=15B→B, resto→C/otros). Conviene revisar la taxonomía periódicamente (§5).
>
> **Notas de implementación:**
> - **R1** usa `PORTFOLIO_VALUE` como denominador del cap (no el capital invertido), para evitar el caso degenerado de que la primera posición sea siempre el 100%.
> - **R2** acota cada sub-tema a `SUBTHEME_MAX_PCT × (cap × PORTFOLIO_VALUE)`, el 40% del *presupuesto* de alta beta del régimen, no del bucket ya lleno.
> - **R3** fija el riesgo objetivo en € (`RISK_PCT × PORTFOLIO_VALUE`), de ahí salen las acciones y se acota a la banda de inversión €500–800 (`size_position`); `MAX_POSITION_PCT` es el techo absoluto de capital. A nivel de trade la inversión manda (el suelo de €500 puede elevar el riesgo sobre el % del régimen); a nivel de cartera el límite duro es R6.
> - **R6** cuenta para **todos los longs** (cualquier tier, no solo alta beta); el riesgo de la cartera existente es el baseline (`open_position_risk`). R6 se evalúa antes que R1/R2 en el mismo bucle de `_apply_exposure_caps`.
> - Los valores de cartera (USD) y `PORTFOLIO_VALUE` (EUR) se tratan a la par (~paridad) en R1/R2/R6 — simplificación heredada; el desajuste EUR/USD (~8%) es conservador y no se corrige aquí.
> - Los ETF de cartera solo cuentan como alta beta si están clasificados explícitamente (un ETF de materias primas/amplio es diversificador).
> - Se añadieron dos sub-temas a los de §2 para agrupar clústeres reales del watchlist: `cripto_fin` (CRCL, SBET…) y `health_spec` (HIMS, OSCR, LMND…).

---

## 5. Falsabilidad — cómo sabremos si funciona

Esta spec es una hipótesis; hay que medirla. A revisar **mensualmente o cada ~30 trades**:

- **Máximo drawdown correlacionado en un día** → debería bajar respecto al −495 del 5-jun.
- **% de alta beta en el libro a lo largo del tiempo** → debe respetar el cap del régimen.
- **Win rate y expectancy por tier (A/B/C)** → ¿qué tier aporta y cuál destruye?
- **Expectancy global** (ganancia media × win% − pérdida media × loss%).

Si tras la revisión el daño correlacionado no baja, o un tier resulta sistemáticamente negativo, se ajustan los parámetros de §4 o se replantea la tesis de §1.

---

## 6. Cuestiones abiertas (decisiones futuras del operador)

- **Riesgo por trade:** ~~resuelto 2026-06-24~~. Antes era fijo (€700–800 en `.env`) sobre un `PORTFOLIO_VALUE` irreal de €10.000 (capital real ≈ €7.000 → ~11 % por trade, inaceptable). Ahora `PORTFOLIO_VALUE` se **lee dinámicamente** de los balances de broker_1+broker_2 en `portfolio.json` (≈€7.200, se ajusta solo), riesgo **dinámico 1–3 %** (R3) con banda de inversión €500–800 y **tope agregado del 10 %** (R6). Pendiente: medir si el rango 1–3 % es el adecuado tras ~30 trades.
- **Ajuste del cap según "growth appetite":** falta definir la señal concreta que sube/baja el cap dentro de los rangos de R1 (¿breadth del mercado? ¿rotación growth vs value? ¿el propio regime del scanner?).
- **Muestra pequeña:** 30 trades no bastan para certificar edge. La spec gestiona riesgo; la validación del edge requiere más histórico o backtest.

---

## 7. Apéndice — Validación de edge (2026-06-13)

Test sobre **196 recomendaciones reales** extraídas de 46 ficheros `daily_state` (15 may – 12 jun), cruzando el `composite_score` generado en vivo contra el retorno forward real (precios yfinance, ajustado por dirección). Sin look-ahead: los scores eran las predicciones de ese día.

**Conclusión: hay edge real pero débil, de swing multi-día, que vive en la cola derecha.**

- **El umbral ≥6 funciona:** a +3/+5 días, score ≥6 rinde +3.2%/+3.5% medio; score <6 rinde −0.4%/−1.1%. La línea de corte separa ganadores de descartes.
- **Es débil:** correlación score→retorno ≈ 0.08 (+1d) → 0.13 (+3d) → 0.15 (+5d). Crece con el horizonte (edge de swing, no intradía; a +1d no hay edge).
- **STRONG BUY no demostrado:** no bate a BUY y solo hay 7 casos. No fiarse más de un STRONG que de un BUY.
- **Win rate < 50% (42.9% global), media +1.67%, mediana −0.19%:** distribución de momentum, cola derecha gorda. La rentabilidad depende de pocos ganadores grandes → cortar ganadores o dejar reventar un cluster correlacionado destruye el edge.
- **Cortos = el edge más fiable:** win 51.3% con media positiva, vs longs 37.5% (que dependen de los pelotazos). Confirma los trades cerrados (cortos 6/7).

**Implicación operativa:** los datos validan esta spec. No tocar indicadores; **proteger la cola** (R1/R2) y **no cortar ganadores pronto** es lo que sostiene la rentabilidad. Pendiente clave: el test cubre un solo régimen (Uptrend/NEUTRAL); falta saber si el edge aguanta en Downtrend.

---

## 8. Filtros de calidad de selección y política de watchlist (2026-07-27)

Motivación: un análisis reveló que el pipeline solo recomendaba tickers A-F. Causa raíz
doble — el screener de descubrimiento ordenaba alfabéticamente (`o=ticker`, corregido a
orden por movimiento del día) y la rotación one-in-one-out persistía momentum y expulsaba
el núcleo curado, degenerando la watchlist. Al hilo, se añadieron filtros de calidad que el
operador aprobó. **Decisiones del operador, no defaults — no revertir sin acordarlo.**

### Política de watchlist — crecimiento con puerta de CALIDAD
> *"La misión de la watchlist es almacenar buenos tickers y operar con ellos. Un ticker malo
> no lo queremos aunque un día se comporte bien: un buen día no basta para entrar."*

- Solo el **`long_screener`** (analyst Strong Buy + insider comprando) puede **promocionar** a
  la watchlist, y solo si `fundamental_score >= WATCHLIST_PROMOTE_MIN_FUND` (7.0).
- Los screeners de **momentum/técnicos** (`ta_weekly_long`, `ta_monthly_breakout`) y `short_screener`
  descubren candidatos para analizar **HOY** pero **NO persisten** (efímeros, como los gappers).
- El **núcleo curado** (`source: manual`) **nunca** se expulsa; la rotación solo recicla entradas
  auto-añadidas por encima de `WATCHLIST_MAX_SIZE` (120). Lógica en `fundamental_analyst._update_watchlist`.

### Filtro A — Suelo de score absoluto (anti-overtrading)
Si ningún candidato es accionable (todos WATCH), el informe titula **"SIN SETUPS ACCIONABLES
HOY"** en vez de presentar WATCH de relleno como si fueran picks. El umbral accionable sigue
siendo ≥6.0 (validado en §7). En `report_writer._format_report_text`.

### Filtro B — Earnings dentro de la ventana de hold
El bloqueo por earnings se extiende de ≤`EARNINGS_BLOCK_DAYS` (3d) a cubrir el hold estimado
(5-10 sesiones): `EARNINGS_HOLD_BLOCK_DAYS` = 12 días naturales. Sostener durante el reporte es
riesgo binario de gap, ingestionable con stop. En `fundamental_analyst._build_result`.

### Filtro C — Suelo de ADX para breakouts
Un setup etiquetado como breakout con **ADX < `ADX_TREND_MIN`** (20) = sin tendencia establecida
→ ruptura probablemente falsa → se degrada a WATCH (motivo visible en el report). No toca
pullbacks/reversiones (ahí un ADX bajo es normal). En `orchestrator._apply_trend_strength_gate`.

### Filtro de beta en el descubrimiento
Los 4 screeners de descubrimiento llevan `Beta: SCREENER_MIN_BETA` ("Over 1"). Coherente
con la tesis de alta beta (§1): un nombre que apenas se mueve no da recorrido de swing
aunque el índice se agite (EWS beta 0.53, BANC 0.74). Solo afecta al DESCUBRIMIENTO — no a
la watchlist curada (los Tier A de baja beta se añaden a mano). No va en los gappers
(event-driven). Cautela: la beta no caza laterales de beta normal (BRKR 1.28, volátil pero
sin tendencia) — de eso se encarga el filtro C (ADX). En `fundamental_analyst` (filter dicts).

### Filtro D — Confirmación multi-agente (PENDIENTE)
Exigir varias patas por encima de un suelo en vez de la media ponderada del composite. Potente
pero **requiere backtest** antes de cablearlo (como R5/R5b) para no cortar la cola de edge.

---

## 9. Integridad de datos — bug de splits sin ajustar (corregido 2026-07-30)

Descubierto al auditar la corrida del 30/07 contra los gráficos de TradingView tras el caso
BE: **`data/market_data.py` pedía barras diarias a Alpaca (`StockBarsRequest`) sin especificar
`adjustment`**, que por defecto viene en `raw` (sin ajustar por split). Cualquier ticker que
hiciera un split dentro de la ventana pedida (1 año para indicadores/high_52w, ~370 días para
el scanner) quedaba con un precipicio de precio en su serie histórica: los precios previos al
split se quedan inflados por el factor del split, los posteriores son los reales.

**Caso confirmado: CRWD.** Split 4:1 el 2026-07-02 (cierre $772,62 el 01/07 → $193,73 el
02/07, sin ninguna noticia que lo justifique). Esto corrompía TODOS los indicadores derivados
de esa serie: `high_52w` salía en **785,59** en vez de **217,50** (verificado contra
TradingView), y EMA9/21/50/200, SMA20/50, Bollinger Bands, ATR, soporte/resistencia y
`base_breakout` quedaban igual de sesgados — el "downtrend brutal" que el TA analyst le
asignó a CRWD el 30/07 era en gran parte un artefacto del split, no una caída real de precio.
Contrastado también META, XPEV y CMCSA en el mismo informe: sin precipicio, datos genuinos.

**Alcance:** no es un bug puntual de CRWD — afecta a **cualquier ticker de la watchlist con un
split** dentro de la ventana de 1 año (indicadores estándar) o de hasta ~6 años (la serie larga
de `_ma200_multiframe` para el MA200 semanal/diario). Sesga sistemáticamente hacia "downtrend"
en el lado post-split (precio real vs. medias infladas por el pre-split), lo que puede
bloquear largos válidos o —peor— habilitar cortos sobre una estructura técnica falsa.

**Fix:** `adjustment=Adjustment.ALL` añadido a los dos `StockBarsRequest` de
`data/market_data.py` (`_fetch_ohlcv_alpaca` y `_fetch_batch_quotes_alpaca`). Verificado en
vivo: `high_52w` de CRWD pasa de 785,59 a 217,355 (coincide con TradingView) y la serie ya no
tiene discontinuidad en la fecha del split.
