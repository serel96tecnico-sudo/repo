import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger(name: str, log_dir: Path = None) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger.addHandler(console)

    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "trading_agent.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=7,
            encoding="utf-8",
        )
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)

    return logger
