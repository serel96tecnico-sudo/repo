import json
import os
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


class ContextManager:
    def __init__(self, context_dir: Path, output_dir: Path):
        self.context_dir = context_dir
        self.output_dir = output_dir
        context_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)

    def save_daily_state(self, state: dict, date: str = None, file_suffix: str = "") -> Path:
        date = date or datetime.now().strftime("%Y-%m-%d")
        path = self.context_dir / f"daily_state_{date}{file_suffix}.json"
        self._atomic_write_json(path, state)
        return path

    def load_daily_state(self, date: str = None) -> dict:
        if date:
            path = self.context_dir / f"daily_state_{date}.json"
            if path.exists():
                return json.loads(path.read_text(encoding="utf-8"))
            return {}

        files = sorted(self.context_dir.glob("daily_state_*.json"), reverse=True)
        if not files:
            return {}
        return json.loads(files[0].read_text(encoding="utf-8"))

    def get_previous_n_days(self, n: int = 5) -> list:
        files = sorted(self.context_dir.glob("daily_state_*.json"), reverse=True)
        results = []
        for f in files[:n]:
            try:
                results.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return results

    def save_watchlist(self, watchlist: dict) -> None:
        self._atomic_write_json(self.context_dir / "watchlist.json", watchlist)

    def load_watchlist(self) -> dict:
        path = self.context_dir / "watchlist.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def load_portfolio(self) -> dict:
        path = self.context_dir / "portfolio.json"
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def save_portfolio(self, portfolio: dict) -> None:
        self._atomic_write_json(self.context_dir / "portfolio.json", portfolio)

    def update_trade_history(self, trade: dict) -> None:
        path = self.context_dir / "trade_history.json"
        history = []
        if path.exists():
            try:
                history = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                history = []
        history.append(trade)
        self._atomic_write_json(path, history)

    def save_ticker_universe(self, tickers: list, source: str) -> None:
        self._atomic_write_json(
            self.context_dir / "ticker_universe.json",
            {"tickers": tickers, "source": source, "updated": datetime.now().isoformat()},
        )

    def load_ticker_universe(self) -> tuple:
        path = self.context_dir / "ticker_universe.json"
        if not path.exists():
            return [], None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            updated = datetime.fromisoformat(data["updated"])
            return data["tickers"], updated
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return [], None

    def cleanup_old_files(self, days_to_keep: int = 30) -> int:
        cutoff = datetime.now() - timedelta(days=days_to_keep)
        deleted = 0
        for pattern in ["daily_state_*.json", "scan_*.json"]:
            for f in self.context_dir.glob(pattern):
                try:
                    date_str = f.stem.rsplit("_", 1)[-1]
                    file_date = datetime.strptime(date_str, "%Y-%m-%d")
                    if file_date < cutoff:
                        f.unlink()
                        deleted += 1
                except (ValueError, OSError):
                    continue
        return deleted

    def load_broker3_state(self) -> dict:
        """Read broker3_positions.json written by the MT4 EA every 5 seconds."""
        mt4_dir = os.getenv("BROKER3_MT4_FILES", "")
        candidates = [
            Path(mt4_dir) / "broker3_positions.json" if mt4_dir else None,
            self.context_dir / "broker3_positions.json",
        ]
        for path in candidates:
            if path and path.exists() and path.stat().st_size > 0:
                try:
                    return json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
        return {}

    def sync_broker3(self) -> bool:
        """Copy broker3_positions.json from MT4 Files folder into contex/."""
        mt4_dir = os.getenv("BROKER3_MT4_FILES", "")
        if not mt4_dir:
            return False
        src = Path(mt4_dir) / "broker3_positions.json"
        dst = self.context_dir / "broker3_positions.json"
        if src.exists() and src.stat().st_size > 0:
            shutil.copy2(src, dst)
            return True
        return False

    def _atomic_write_json(self, path: Path, data) -> None:
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        os.replace(tmp_path, path)
