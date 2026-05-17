import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        description="Swing Trading Agent — daily stock scanner using Claude AI"
    )
    parser.add_argument("--schedule", action="store_true", help="Run on daily schedule (weekdays)")
    parser.add_argument("--setup-scheduler", action="store_true", help="Install Windows Task Scheduler tasks (morning + evening)")
    parser.add_argument("--test", action="store_true", help="Dry run with mock market data")
    parser.add_argument("--tickers", nargs="+", metavar="TICKER", help="Analyze specific tickers only")
    parser.add_argument("--dry-run", action="store_true", help="Skip Claude API calls (test pipeline only)")
    parser.add_argument("--session", choices=["morning", "evening"], default="morning",
                        help="Session label: morning (15:00) or evening (20:30)")
    parser.add_argument("--webhook", action="store_true", help="Start TradingView webhook server")
    parser.add_argument("--portfolio-report", action="store_true", help="Generar informe de cartera y P&L mensual")
    parser.add_argument("--close-trade", nargs="+", metavar="ARG", help="Registrar operación cerrada: TICKER EXIT_PRICE [NOTAS]")
    args = parser.parse_args()

    if args.portfolio_report:
        from agents.portfolio_tracker import PortfolioTracker
        PortfolioTracker().run()
        return

    if args.close_trade:
        from agents.portfolio_tracker import PortfolioTracker
        import json
        from pathlib import Path
        args_ct = args.close_trade
        if len(args_ct) < 2:
            print("Uso: --close-trade TICKER EXIT_PRICE [NOTAS]")
            return
        ticker = args_ct[0].upper()
        exit_price = float(args_ct[1])
        notas = " ".join(args_ct[2:]) if len(args_ct) > 2 else ""
        portfolio = json.loads(Path("contex/portfolio.json").read_text(encoding="utf-8"))
        pos = next((p for p in portfolio.get("acciones", []) if p["ticker"].upper() == ticker), None)
        if not pos:
            print(f"No se encontró {ticker} en portfolio.json")
            return
        tracker = PortfolioTracker()
        moneda = pos.get("moneda", "USD")
        entry = pos.get("bep_usd") or pos.get("bep_eur") or 0
        trade = tracker.log_closed_trade(
            broker=pos["broker"],
            ticker=ticker,
            nombre=pos.get("nombre", ticker),
            direccion=pos.get("direccion", "long"),
            cantidad=pos.get("cantidad", 0),
            entry_date=pos.get("entry_date", ""),
            entry_price=entry,
            exit_date=__import__("datetime").datetime.now().strftime("%Y-%m-%d"),
            exit_price=exit_price,
            moneda=moneda,
            notas=notas,
        )
        moneda_sym = "€" if moneda == "EUR" else "$"
        net = trade.get("net_pnl_usd") or trade.get("net_pnl_eur") or 0
        print(f"Operación cerrada: {ticker} | Neto: {moneda_sym}{net:+.2f} | {trade['resultado'].upper()}")
        print("Recuerda eliminar la posición de portfolio.json manualmente.")
        return

    if args.webhook:
        from webhook_server import app
        from config import WEBHOOK_PORT
        print(f"Webhook server en http://localhost:{WEBHOOK_PORT}/webhook")
        print("Exponer a internet: ngrok http 5000")
        app.run(host="0.0.0.0", port=WEBHOOK_PORT, debug=False)
        return

    if args.setup_scheduler:
        from setup_task_scheduler import create_all_tasks
        create_all_tasks()
        return

    if args.schedule:
        from scheduler import start_scheduler
        start_scheduler()
        return

    if args.test:
        from tests.test_e2e import run_dry_run
        run_dry_run()
        return

    from agents.orchestrator import TradingOrchestrator
    orch = TradingOrchestrator(override_tickers=args.tickers, dry_run=args.dry_run, session=args.session)
    report = orch.run_daily_pipeline()
    if report:
        print(f"\nReport saved to: {report.report_txt_path}")
        print(f"JSON saved to:   {report.report_json_path}")
        print(f"\nTop candidates:")
        for fc in report.candidates[:5]:
            print(f"  #{fc.rank} {fc.ticker} — {fc.recommendation} (score: {fc.composite_score:.1f})")


if __name__ == "__main__":
    main()
