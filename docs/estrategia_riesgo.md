# Especificación de Estrategia y Riesgo

> **Estado:** v1 — 2026-06-13. Primera spec de estrategia definida explícitamente por el operador (no heredada de defaults del modelo).
> **Implementación:** R1/R2/R3 cableadas en código el 2026-06-15 (`config.py` §4 + `utils/risk_policy.py`, aplicadas en `risk_manager.py` y `orchestrator._apply_exposure_caps`). R4 ya vivía en el orchestrator. Tests en `tests/test_risk_policy.py`.
> **Ámbito:** política de exposición, concentración y sizing. NO toca los indicadores técnicos del pipeline.

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

### R3 — Half-size en volatilidad elevada
Cuando el régimen es **NEUTRAL/Sideways y VIX ≥ 20**, los **nuevos longs de Tier C** entran a **50 % del riesgo normal** (≈ €250–300 en vez de €500–600).

> Tier A y B mantienen tamaño normal. Solo se frena lo más especulativo cuando el sistema ya marca "volatilidad elevada".

### R4 — Cortos (sin cambios estructurales)
Los cortos realizados funcionaron (6/7 ganadores, +207). Se mantienen las reglas vigentes: solo Broker 2, gated por régimen (no abrir en *Strong Uptrend*), entrada en pullback a EMA9. Esta spec no los modifica.

---

## 4. Parámetros (implementados en `config.py` desde 2026-06-15)

```
# Caps de exposición alta beta por régimen
HIGH_BETA_CAP_STRONG_UP   = 0.80
HIGH_BETA_CAP_UPTREND     = 0.70
HIGH_BETA_CAP_NEUTRAL     = 0.60
HIGH_BETA_CAP_RISKOFF     = 0.40

# Concentración por sub-tema dentro del bucket alta beta
SUBTHEME_MAX_PCT          = 0.40

# Half-size
NEUTRAL_VIX_THRESHOLD     = 20
HALF_SIZE_FACTOR          = 0.50
```

> Estos valores viven aquí como **decisión** y están replicados en `config.py`. La clasificación de tiers (§2) y sub-temas vive en `utils/risk_policy.py` como **semilla editable**: los nombres no listados se clasifican por capitalización (>=100B→A, >=15B→B, resto→C/otros). Conviene revisar la taxonomía periódicamente (§5).
>
> **Notas de implementación:**
> - **R1** usa `PORTFOLIO_VALUE` como denominador del cap (no el capital invertido), para evitar el caso degenerado de que la primera posición sea siempre el 100%.
> - **R2** acota cada sub-tema a `SUBTHEME_MAX_PCT × (cap × PORTFOLIO_VALUE)`, el 40% del *presupuesto* de alta beta del régimen, no del bucket ya lleno.
> - **R3** reduce el riesgo objetivo (no el tope de capital): si ya estabas limitado por capital, el half-size puede no cambiar el nº de acciones.
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

- **Riesgo por trade:** actualmente €500–600 sobre €10.000 = **5–6 % por operación**, que es agresivo. Con el cap de concentración el riesgo de cartera baja, pero conviene revisar si este % por trade sigue siendo el deseado.
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
