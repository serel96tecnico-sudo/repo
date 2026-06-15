import time
import schedule
from datetime import datetime

from config import RUN_TIME, RUN_TIME_EVENING
from utils.logger import get_logger

logger = get_logger("Scheduler")


def run_pipeline(session="morning"):
    logger.info(f"Scheduled {session} run triggered at {datetime.now()}")
    try:
        from agents.orchestrator import TradingOrchestrator
        orch = TradingOrchestrator(session=session)
        report = orch.run_daily_pipeline()
        if report:
            logger.info(f"Completed ({session}). Report: {report.report_txt_path}")
        else:
            logger.info(f"No report generated ({session}) (market closed or no candidates).")
    except Exception as e:
        logger.error(f"Pipeline error ({session}): {e}", exc_info=True)


def start_scheduler():
    logger.info(
        f"Scheduler started. Will run weekdays: morning {RUN_TIME}, "
        f"evening {RUN_TIME_EVENING} (local time)."
    )
    for day in ("monday", "tuesday", "wednesday", "thursday", "friday"):
        getattr(schedule.every(), day).at(RUN_TIME).do(run_pipeline, session="morning")
        getattr(schedule.every(), day).at(RUN_TIME_EVENING).do(run_pipeline, session="evening")

    while True:
        schedule.run_pending()
        time.sleep(30)
