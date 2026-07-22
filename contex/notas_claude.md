# Notas persistentes de Claude — memoria operativa

> Memoria manual y fiable entre sesiones para el trading-agent. La mantiene Claude.
> Se carga siempre porque `CLAUDE.md` la referencia. Aquí van workflows, decisiones y
> gotchas que conviene tener a mano en **todas** las sesiones, sin depender de claude-mem.
>
> Reglas para Claude: añadir aquí lo importante de forma proactiva; notas en español;
> fechar cada entrada; mantenerlo conciso (podar lo que quede obsoleto).

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
