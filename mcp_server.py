"""
MCP Server — Trading Agent
Expone el pipeline de trading como herramientas para Claude Code.

Herramientas disponibles:
  - get_market_overview      Condiciones actuales del mercado (SPY/QQQ/VIX)
  - get_portfolio            Lee el portfolio actual
  - update_portfolio         Actualiza posiciones en portfolio.json
  - get_watchlist            Lee el watchlist personalizado
  - update_watchlist         Reemplaza el watchlist (o lo borra para volver al universo completo)
  - get_last_report          Texto del último reporte generado
  - get_daily_state          Estado persistido del último pipeline
  - analyze_ticker           Pipeline completo sobre tickers específicos
  - scan_market              Pipeline completo sobre el universo entero
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("trading-agent")


# ---------------------------------------------------------------------------
# Herramientas de consulta rápida (sin Claude API)
# ---------------------------------------------------------------------------

@mcp.tool()
def get_market_overview() -> str:
    """Devuelve las condiciones actuales del mercado: tendencia SPY/QQQ, nivel VIX y régimen de mercado."""
    from config import CONTEXT_DIR
    from data.market_data import MarketDataFetcher
    try:
        fetcher = MarketDataFetcher(CONTEXT_DIR)
        market = fetcher.get_market_overview()
        return json.dumps(market.to_dict(), indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error obteniendo datos de mercado: {e}"


@mcp.tool()
def get_portfolio() -> str:
    """Lee el portfolio actual desde contex/portfolio.json. Incluye acciones, ETFs y resumen de cuenta."""
    from config import CONTEXT_DIR, OUTPUT_DIR
    from utils.context_manager import ContextManager
    try:
        ctx = ContextManager(CONTEXT_DIR, OUTPUT_DIR)
        portfolio = ctx.load_portfolio()
        if not portfolio:
            return "No se encontró portfolio. Crea contex/portfolio.json para registrar posiciones."
        return json.dumps(portfolio, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error leyendo portfolio: {e}"


@mcp.tool()
def update_portfolio(portfolio_json: str) -> str:
    """
    Actualiza el portfolio completo. Recibe el portfolio como string JSON.
    El schema debe seguir la estructura de contex/portfolio.json existente.
    """
    from config import CONTEXT_DIR, OUTPUT_DIR
    from utils.context_manager import ContextManager
    try:
        portfolio = json.loads(portfolio_json)
        ctx = ContextManager(CONTEXT_DIR, OUTPUT_DIR)
        ctx.save_portfolio(portfolio)
        tickers = [p["ticker"] for p in portfolio.get("acciones", []) + portfolio.get("etfs", [])]
        return f"Portfolio actualizado. Posiciones: {', '.join(tickers) if tickers else 'ninguna'}"
    except json.JSONDecodeError as e:
        return f"Error: JSON inválido — {e}"
    except Exception as e:
        return f"Error actualizando portfolio: {e}"


@mcp.tool()
def get_watchlist() -> str:
    """
    Lee el watchlist personalizado desde contex/watchlist.json.
    Si no existe, el pipeline usa el universo completo S&P 500 + NDX 100.
    """
    from config import CONTEXT_DIR, OUTPUT_DIR
    from utils.context_manager import ContextManager
    try:
        ctx = ContextManager(CONTEXT_DIR, OUTPUT_DIR)
        watchlist = ctx.load_watchlist()
        if not watchlist or not watchlist.get("tickers"):
            return "Sin watchlist personalizado. Se usa el universo completo S&P 500 + NDX 100."
        tickers = watchlist["tickers"]
        return f"{len(tickers)} tickers en watchlist:\n{', '.join(tickers)}"
    except Exception as e:
        return f"Error leyendo watchlist: {e}"


@mcp.tool()
def update_watchlist(tickers: list[str]) -> str:
    """
    Reemplaza el watchlist personalizado. Cuando está activo, solo se escanean estos tickers.
    Pasa una lista vacía para volver al universo completo S&P 500 + NDX 100.
    """
    from config import CONTEXT_DIR, OUTPUT_DIR
    from utils.context_manager import ContextManager
    try:
        ctx = ContextManager(CONTEXT_DIR, OUTPUT_DIR)
        if tickers:
            normalized = [t.upper().strip() for t in tickers if t.strip()]
            ctx.save_watchlist({"tickers": normalized})
            preview = ", ".join(normalized[:10])
            suffix = f"... (+{len(normalized)-10} más)" if len(normalized) > 10 else ""
            return f"Watchlist actualizado: {len(normalized)} tickers — {preview}{suffix}"
        else:
            path = CONTEXT_DIR / "watchlist.json"
            if path.exists():
                path.unlink()
            return "Watchlist eliminado. Se usará el universo completo S&P 500 + NDX 100."
    except Exception as e:
        return f"Error actualizando watchlist: {e}"


@mcp.tool()
def get_last_report(session: str = "morning") -> str:
    """
    Devuelve el texto del reporte más reciente.
    session: 'morning' o 'evening'
    """
    from config import OUTPUT_DIR
    output_dir = Path(OUTPUT_DIR)
    try:
        suffix = "_evening" if session == "evening" else ""
        files = sorted(output_dir.glob(f"report_*{suffix}.txt"), reverse=True)
        if not files:
            return f"No se encontró ningún reporte de sesión '{session}' en {output_dir}"
        latest = files[0]
        return f"Reporte: {latest.name}\n\n{latest.read_text(encoding='utf-8')}"
    except Exception as e:
        return f"Error leyendo reporte: {e}"


@mcp.tool()
def get_daily_state(date: str = None) -> str:
    """
    Devuelve el estado persistido del pipeline (candidatos, mercado, resumen de scan).
    date: fecha en formato YYYY-MM-DD. Si se omite, devuelve el más reciente.
    """
    from config import CONTEXT_DIR, OUTPUT_DIR
    from utils.context_manager import ContextManager
    try:
        ctx = ContextManager(CONTEXT_DIR, OUTPUT_DIR)
        state = ctx.load_daily_state(date)
        if not state:
            msg = f"No hay estado para la fecha {date}." if date else "No hay estado guardado aún."
            return msg
        return json.dumps(state, indent=2, ensure_ascii=False)
    except Exception as e:
        return f"Error leyendo estado diario: {e}"


# ---------------------------------------------------------------------------
# Herramientas de análisis (invocan Claude API — tardan varios minutos)
# ---------------------------------------------------------------------------

@mcp.tool()
def analyze_ticker(tickers: list[str], session: str = "morning", dry_run: bool = False) -> str:
    """
    Ejecuta el pipeline completo (scan → TA → sentimiento → riesgo → reporte) sobre tickers específicos.
    Devuelve candidatos rankeados con scores, niveles de entrada/stop/target y tamaño de posición.
    Tarda ~3-5 minutos por ticker debido a las llamadas a la API de Claude.

    tickers: lista de símbolos (ej. ["NVDA", "AAPL"])
    session: 'morning' o 'evening' (afecta el nombre del archivo de reporte)
    dry_run: si True, omite las llamadas a Claude (solo prueba el pipeline de datos)
    """
    from agents.orchestrator import TradingOrchestrator

    if not tickers:
        return "Error: proporciona al menos un ticker."

    normalized = [t.upper().strip() for t in tickers if t.strip()]

    try:
        orch = TradingOrchestrator(
            override_tickers=normalized,
            dry_run=dry_run,
            session=session,
        )
        report = orch.run_daily_pipeline()

        if not report:
            return "El pipeline no generó reporte. El mercado puede estar cerrado o no hay setups válidos."

        lines = [
            f"Análisis completado — {len(report.candidates)} candidato(s) en {report.generation_time_seconds:.0f}s",
            f"Mercado: {report.market_conditions.spy_trend} | VIX: {report.market_conditions.vix_level} | {report.market_conditions.regime}",
            "",
        ]

        for fc in report.candidates:
            risk = fc.risk_data
            ta = fc.ta_data
            lines.append(f"#{fc.rank} {fc.ticker} — {fc.recommendation} (score: {fc.composite_score:.1f})")
            if risk:
                dir_label = "SHORT" if (ta and ta.direction == "short") else "LONG"
                lines.append(
                    f"  [{dir_label}] Entrada: ${risk.entry_price:.2f} | Stop: ${risk.stop_loss:.2f} | "
                    f"TP1: ${risk.target_1:.2f} (R:R {risk.rr_ratio_1:.1f}:1) | TP2: ${risk.target_2:.2f} (R:R {risk.rr_ratio_2:.1f}:1)"
                )
                lines.append(
                    f"  Tamaño: {risk.position_size_shares} acciones ({risk.position_size_pct:.1f}% cartera) | "
                    f"Pérdida máx: ${risk.max_loss_dollars:.0f}"
                )
            if ta:
                lines.append(f"  Patrón: {ta.pattern_detected} | Trigger: {ta.entry_trigger}")
                if ta.ta_summary:
                    lines.append(f"  {ta.ta_summary}")
            if fc.summary:
                lines.append(f"  → {fc.summary}")
            lines.append("")

        lines.append(f"Reporte completo: {report.report_txt_path}")
        return "\n".join(lines)

    except ValueError as e:
        return f"Error de configuración: {e}"
    except Exception as e:
        return f"Error en pipeline: {e}"


@mcp.tool()
def scan_market(session: str = "morning") -> str:
    """
    Ejecuta el pipeline diario completo sobre el universo entero de tickers.
    Escanea S&P 500 + NDX 100 (o watchlist personalizado), filtra candidatos y genera reporte rankeado.
    Tarda 15-25 minutos por la descarga de datos y múltiples llamadas a la API de Claude.

    session: 'morning' o 'evening'
    """
    from agents.orchestrator import TradingOrchestrator

    try:
        orch = TradingOrchestrator(session=session)
        report = orch.run_daily_pipeline()

        if not report:
            return "No se generó reporte. El mercado puede estar cerrado hoy (fin de semana o festivo)."

        lines = [
            f"Scan completado — {report.total_scanned} tickers escaneados, "
            f"{report.total_analyzed} analizados en profundidad | {report.generation_time_seconds:.0f}s",
            f"Mercado: {report.market_conditions.spy_trend} | VIX: {report.market_conditions.vix_level} | {report.market_conditions.regime}",
            "",
            "Top candidatos:",
        ]

        for fc in report.candidates[:7]:
            risk = fc.risk_data
            ta = fc.ta_data
            dir_label = ""
            if ta:
                dir_label = " [SHORT]" if ta.direction == "short" else " [LONG]"
            lines.append(f"#{fc.rank} {fc.ticker} ({fc.company_name}){dir_label} — {fc.recommendation} (score: {fc.composite_score:.1f})")
            if risk:
                lines.append(
                    f"  Entrada: ${risk.entry_price:.2f} | Stop: ${risk.stop_loss:.2f} | "
                    f"TP1: ${risk.target_1:.2f} | TP2: ${risk.target_2:.2f}"
                )

        lines.append(f"\nReporte completo: {report.report_txt_path}")
        return "\n".join(lines)

    except ValueError as e:
        return f"Error de configuración: {e}"
    except Exception as e:
        return f"Error en scan: {e}"


if __name__ == "__main__":
    mcp.run()
