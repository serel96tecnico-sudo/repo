"""
draw_levels.py — Dibuja en TradingView Desktop las marcas de entrada/stop/targets
de las recomendaciones del pipeline, SOLO para los candidatos BUY con score >= 6.

Uso:
    python draw_levels.py                 # usa el report_*.json más reciente
    python draw_levels.py <ruta_report>   # usa un JSON concreto

Requisitos:
    - TradingView Desktop abierto (el usuario ya lo abre antes del pipeline).
    - Node.js en el PATH y el puente tradingview-mcp instalado.

Se apoya en el CLI `tv` (tradingview-mcp) vía CDP. Un ticker "pelado" (NVDA, TSM)
se resuelve solo a su exchange primario — por eso NO hace falta mapear exchanges.

Es NO-FATAL por diseño: si TradingView no está abierto o el CLI falla, avisa y
sale sin romper nada (la señal por Telegram no depende de esto).
"""
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# La consola de Windows por defecto es cp1252 y no sabe codificar los caracteres
# no-ASCII de los mensajes de progreso (✔, ·, →, ⚠). Bajo Task Scheduler eso
# provoca un UnicodeEncodeError que aborta el dibujado a medias. Forzamos UTF-8
# con errores tolerantes para que un print nunca pueda tumbar el proceso.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# --- Configuración ------------------------------------------------------------
ROOT = Path(__file__).parent
OUTPUT_DIR = ROOT / "output"
STATE_FILE = ROOT / "contex" / "tv_drawn_levels.json"

# Directorio del puente tradingview-mcp (override por env TV_MCP_DIR).
TV_MCP_DIR = Path(os.getenv("TV_MCP_DIR", r"C:\Users\bucki\tradingview-mcp"))
TV_CLI = TV_MCP_DIR / "src" / "cli" / "index.js"


def _find_node():
    """Resuelve el ejecutable de node. El Task Scheduler lanza con un PATH mínimo,
    así que 'node' a secas puede no encontrarse aunque funcione en un shell normal
    (este era el motivo de que el dibujado fallara solo en el run programado).
    Buscamos en el PATH y, si no, en las rutas de instalación típicas de Windows."""
    found = shutil.which("node")
    if found:
        return found
    for p in (r"C:\Program Files\nodejs\node.exe",
              r"C:\Program Files (x86)\nodejs\node.exe"):
        if os.path.exists(p):
            return p
    return "node"  # último recurso; si de verdad falta, tv() lo reporta


NODE_BIN = _find_node()

MIN_SCORE = 6.0                       # solo BUY con composite_score >= esto
BUY_RECOMMENDATIONS = {"BUY", "STRONG BUY"}
DRAW_TARGETS = True                   # entrada+stop siempre; targets T1/T2 opcional

# Colores/estilos de las líneas (linestyle: 0=sólida, 2=discontinua)
STYLE_ENTRY = {"linecolor": "#26a69a", "linewidth": 2, "linestyle": 0, "showLabel": True}
STYLE_STOP = {"linecolor": "#ef5350", "linewidth": 2, "linestyle": 0, "showLabel": True}
STYLE_TARGET = {"linecolor": "#2962ff", "linewidth": 1, "linestyle": 2, "showLabel": True}


def tv(*args, timeout=30):
    """Ejecuta el CLI `tv` y devuelve el JSON parseado (o {} si falla)."""
    try:
        proc = subprocess.run(
            [NODE_BIN, str(TV_CLI), *args],
            cwd=str(TV_MCP_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError(f"No se encontró node (probado: {NODE_BIN}). "
                           "Instala Node.js o define su ruta.")
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "timeout"}
    out = (proc.stdout or "").strip()
    try:
        return json.loads(out)
    except (json.JSONDecodeError, ValueError):
        return {"success": proc.returncode == 0, "raw": out, "stderr": (proc.stderr or "").strip()}


def draw_line(price, style, text):
    ov = dict(style)
    ov["text"] = text
    return tv(
        "draw", "shape",
        "--type", "horizontal_line",
        "--price", str(price),
        "--time", str(int(time.time())),
        "--overrides", json.dumps(ov),
    )


def find_latest_report():
    reports = sorted(OUTPUT_DIR.glob("report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8")).get("symbols", [])
    except Exception:
        return []


def save_state(symbols):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"symbols": symbols, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def main(report_arg=None):
    # report_arg explícito (CLI) o autodetección. NO leer sys.argv aquí: cuando
    # main.py hace `import draw_levels; draw_levels.main()`, sys.argv son los flags
    # del pipeline (--session evening) y romperían la detección del report.
    report_path = Path(report_arg) if report_arg else find_latest_report()
    if not report_path or not report_path.exists():
        print("No se encontró ningún report_*.json en output/.")
        return 1

    data = json.loads(report_path.read_text(encoding="utf-8"))
    candidates = data.get("candidates", [])

    # Filtro: BUY/STRONG BUY con score >= MIN_SCORE
    picks = [
        c for c in candidates
        if c.get("recommendation", "").upper() in BUY_RECOMMENDATIONS
        and float(c.get("composite_score", 0)) >= MIN_SCORE
    ]

    print(f"Report: {report_path.name}")
    print(f"Candidatos BUY >= {MIN_SCORE}: {len(picks)}"
          + (f" ({', '.join(c['ticker'] for c in picks)})" if picks else ""))

    # Verifica conexión con TradingView antes de tocar nada.
    state = tv("state")
    if not state.get("success"):
        print("⚠ TradingView Desktop no responde (¿está abierto?). No se dibuja nada.")
        return 2
    original_symbol = state.get("symbol")

    previous = load_state()
    current_tickers = [c["ticker"] for c in picks]

    # 1) Limpia marcas de símbolos que ya NO están en la lista de hoy.
    for sym in previous:
        if sym not in current_tickers:
            tv("symbol", sym)
            tv("draw", "clear")

    # 2) Dibuja los de hoy (limpiando antes cada uno para no acumular).
    drawn = []
    for c in picks:
        ticker = c["ticker"]
        rd = c.get("risk_data") or {}
        entry = rd.get("entry_price")
        stop = rd.get("stop_loss")
        t1, t2 = rd.get("target_1"), rd.get("target_2")
        score = c.get("composite_score")

        set_res = tv("symbol", ticker)
        if not set_res.get("success"):
            print(f"  x {ticker}: no se pudo fijar el símbolo, salto.")
            continue
        tv("draw", "clear")

        if entry is not None:
            draw_line(entry, STYLE_ENTRY, f"ENTRADA {entry} [{ticker} {score}]")
        if stop is not None:
            draw_line(stop, STYLE_STOP, f"STOP {stop}")
        if DRAW_TARGETS and t1 is not None:
            draw_line(t1, STYLE_TARGET, f"T1 {t1}")
        if DRAW_TARGETS and t2 is not None:
            draw_line(t2, STYLE_TARGET, f"T2 {t2}")

        drawn.append(ticker)
        print(f"  ✔ {ticker}: entrada {entry} · stop {stop}"
              + (f" · T1 {t1} · T2 {t2}" if DRAW_TARGETS else ""))

    save_state(drawn)

    # 3) Restaura el símbolo que tenía el usuario.
    if original_symbol:
        tv("symbol", original_symbol)

    print(f"Hecho. Marcadas {len(drawn)} acciones en TradingView.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else None))
