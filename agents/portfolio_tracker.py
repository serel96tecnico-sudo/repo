import json
import os
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from config import CONTEXT_DIR, OUTPUT_DIR, BROKER2_COMMISSION
from utils.logger import get_logger


LOGS_DIR = Path("output/logs")


class PortfolioTracker:
    def __init__(self):
        self.logger = get_logger("PortfolioTracker", LOGS_DIR)
        self.history_path = CONTEXT_DIR / "trade_history.json"
        self.portfolio_path = CONTEXT_DIR / "portfolio.json"

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    def run(self) -> str:
        portfolio = self._load_portfolio()
        history = self._load_history()
        report = self._build_report(portfolio, history)
        self._save_report(report)
        print(report.encode("ascii", errors="replace").decode("ascii"))
        return report

    def log_closed_trade(
        self,
        broker: str,
        ticker: str,
        nombre: str,
        direccion: str,
        cantidad: int,
        entry_date: str,
        entry_price: float,
        exit_date: str,
        exit_price: float,
        moneda: str = "USD",
        notas: str = "",
    ) -> dict:
        """Registra una operación cerrada en trade_history.json."""
        history = self._load_history()

        gross = round((exit_price - entry_price) * cantidad if direccion == "long"
                      else (entry_price - exit_price) * cantidad, 2)
        commission = BROKER2_COMMISSION if broker == "broker_2" else 0.0
        net = round(gross - commission, 2)

        trade = {
            "id": str(uuid.uuid4())[:8],
            "broker": broker,
            "ticker": ticker,
            "nombre": nombre,
            "direccion": direccion,
            "cantidad": cantidad,
            "entry_date": entry_date,
            "entry_price_usd": entry_price if moneda == "USD" else None,
            "entry_price_eur": entry_price if moneda == "EUR" else None,
            "exit_date": exit_date,
            "exit_price_usd": exit_price if moneda == "USD" else None,
            "exit_price_eur": exit_price if moneda == "EUR" else None,
            "moneda": moneda,
            "gross_pnl_usd": gross if moneda == "USD" else None,
            "gross_pnl_eur": gross if moneda == "EUR" else None,
            "commission_usd": commission if moneda == "USD" else 0.0,
            "net_pnl_usd": net if moneda == "USD" else None,
            "net_pnl_eur": net if moneda == "EUR" else None,
            "resultado": "win" if net > 0 else "loss",
            "notas": notas,
        }

        history["trades"].append(trade)
        self._save_history(history)
        self.logger.info(f"Operación cerrada registrada: {ticker} net P&L {moneda} {net:+.2f}")
        return trade

    # ------------------------------------------------------------------ #
    #  Report builder                                                      #
    # ------------------------------------------------------------------ #

    def _build_report(self, portfolio: dict, history: dict) -> str:
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        current_month = now.strftime("%Y-%m")

        trades = history.get("trades", [])
        brokers = portfolio.get("brokers", {})
        positions = portfolio.get("acciones", [])
        etfs = portfolio.get("etfs", [])

        lines = [
            "=" * 60,
            f"INFORME DE CARTERA — {today}",
            "=" * 60,
        ]

        # ---- Saldos de cuentas ----
        b1 = brokers.get("broker_1", {})
        b2 = brokers.get("broker_2", {})
        lines += [
            "",
            "SALDOS DE CUENTA",
            "-" * 40,
            f"Broker 1 (EUR):",
            f"  Cuenta total:    €{b1.get('cuenta_completa_eur', 0):,.2f}",
            f"  Margen libre:    €{b1.get('margen_libre_eur', 0):,.2f}",
            f"  Cartera valor:   €{b1.get('cartera_eur', 0):,.2f}",
            f"  B/P total:       €{b1.get('total_bp_eur', 0):+,.2f}",
            "",
            f"Broker 2 (USD):",
            f"  Balance:         ${b2.get('balance_usd', 0):,.2f}",
            f"  Disponible:      ${b2.get('available_funds_usd', 0):,.2f}",
            f"  Proyectado:      ${b2.get('projected_balance_usd', 0):,.2f}",
        ]

        # ---- Posiciones abiertas ----
        lines += ["", "POSICIONES ABIERTAS", "-" * 40]

        open_pnl_eur = 0.0
        open_pnl_usd = 0.0

        all_open = [p for p in positions + etfs if p.get("broker") == "broker_1"]
        if all_open:
            lines.append("  Broker 1:")
            for p in all_open:
                ticker = p.get("ticker", "?")
                qty = p.get("cantidad", 0) or 0
                moneda = p.get("moneda", "USD")
                sl = p.get("stop_loss")
                tp = p.get("take_profit")
                pl = p.get("net_pl_eur") or p.get("net_pl_usd") or 0.0
                sym = "€" if moneda == "EUR" else "$"
                bep = p.get("bep_eur") or p.get("bep_usd") or 0
                sl_str = f"SL {sym}{sl}" if sl else "—"
                tp_str = f"TP {sym}{tp}" if tp else "—"
                lines.append(f"    {ticker:<8} {qty:>4} acc | BEP {sym}{bep:.2f} | {sl_str} | {tp_str} | P/L: {sym}{pl:+.2f}")
                if moneda == "EUR":
                    open_pnl_eur += pl
                else:
                    open_pnl_usd += pl

        b2_open = [p for p in positions if p.get("broker") == "broker_2" and p.get("cantidad")]
        if b2_open:
            lines.append("  Broker 2:")
            for p in b2_open:
                ticker = p.get("ticker", "?")
                qty = p.get("cantidad", 0) or 0
                sl = p.get("stop_loss")
                tp = p.get("take_profit")
                pl = p.get("net_pl_usd") or 0.0
                bep = p.get("bep_usd") or 0
                direccion = p.get("direccion", "long").upper()
                sl_str = f"SL ${sl}" if sl else "—"
                tp_str = f"TP ${tp}" if tp else "—"
                lines.append(f"    {ticker:<8} {qty:>4} acc [{direccion}] | BEP ${bep:.2f} | {sl_str} | {tp_str} | P/L: ${pl:+.2f}")
                open_pnl_usd += pl

        lines += [
            "",
            f"  P/L no realizado total:  €{open_pnl_eur:+.2f} | ${open_pnl_usd:+.2f}",
        ]

        # ---- P&L mensual (operaciones cerradas) ----
        lines += ["", "P&L MENSUAL (operaciones cerradas)", "-" * 40]

        monthly = defaultdict(lambda: {"trades": [], "net_usd": 0.0, "net_eur": 0.0, "wins": 0, "losses": 0})
        for t in trades:
            month = (t.get("exit_date") or "")[:7]
            if not month:
                continue
            monthly[month]["trades"].append(t)
            monthly[month]["net_usd"] += t.get("net_pnl_usd") or 0.0
            monthly[month]["net_eur"] += t.get("net_pnl_eur") or 0.0
            if t.get("resultado") == "win":
                monthly[month]["wins"] += 1
            else:
                monthly[month]["losses"] += 1

        if monthly:
            for month in sorted(monthly.keys(), reverse=True):
                m = monthly[month]
                n = len(m["trades"])
                wr = round(m["wins"] / n * 100) if n > 0 else 0
                usd_str = f"${m['net_usd']:+.2f}" if m["net_usd"] != 0 else ""
                eur_str = f"€{m['net_eur']:+.2f}" if m["net_eur"] != 0 else ""
                pnl_str = " | ".join(filter(None, [usd_str, eur_str]))
                lines.append(f"  {month}:  {n} ops | Win rate {wr}% ({m['wins']}W/{m['losses']}L) | Neto: {pnl_str}")
        else:
            lines.append("  Sin operaciones cerradas registradas.")

        # ---- Historial reciente ----
        lines += ["", "ÚLTIMAS OPERACIONES CERRADAS", "-" * 40]
        recent = sorted(trades, key=lambda t: t.get("exit_date", ""), reverse=True)[:10]
        if recent:
            for t in recent:
                sym = "€" if t.get("moneda") == "EUR" else "$"
                net = t.get("net_pnl_usd") or t.get("net_pnl_eur") or 0.0
                resultado = "[W]" if t.get("resultado") == "win" else "[L]"
                lines.append(
                    f"  {resultado} {t.get('exit_date','?')} | {t.get('ticker','?'):<6} "
                    f"[{t.get('direccion','long')}] {t.get('cantidad','?')} acc | "
                    f"Neto: {sym}{net:+.2f} | {t.get('broker','?').replace('broker_','B')}"
                )
        else:
            lines.append("  Sin historial aún.")

        # ---- Totales acumulados ----
        total_usd = sum((t.get("net_pnl_usd") or 0) for t in trades)
        total_eur = sum((t.get("net_pnl_eur") or 0) for t in trades)
        n_total = len(trades)
        wins_total = sum(1 for t in trades if t.get("resultado") == "win")

        lines += [
            "",
            "RESUMEN ACUMULADO (operaciones cerradas)",
            "-" * 40,
            f"  Total operaciones:  {n_total}",
            f"  Win rate global:    {round(wins_total/n_total*100) if n_total else 0}% ({wins_total}W/{n_total-wins_total}L)",
            f"  P&L neto USD:       ${total_usd:+.2f}",
            f"  P&L neto EUR:       €{total_eur:+.2f}",
            "",
            "=" * 60,
            "Actualizado: " + datetime.now().strftime("%Y-%m-%d %H:%M"),
            "=" * 60,
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    #  I/O helpers                                                         #
    # ------------------------------------------------------------------ #

    def _load_portfolio(self) -> dict:
        try:
            return json.loads(self.portfolio_path.read_text(encoding="utf-8"))
        except Exception as e:
            self.logger.error(f"No se pudo leer portfolio.json: {e}")
            return {}

    def _load_history(self) -> dict:
        if not self.history_path.exists():
            return {"trades": []}
        try:
            return json.loads(self.history_path.read_text(encoding="utf-8"))
        except Exception:
            return {"trades": []}

    def _save_history(self, history: dict) -> None:
        tmp = self.history_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.history_path)

    def _save_report(self, report: str) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        path = OUTPUT_DIR / f"portfolio_report_{today}.txt"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(report, encoding="utf-8")
        self.logger.info(f"Informe de cartera guardado: {path}")
