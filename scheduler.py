import time
import schedule
from datetime import datetime

from config import RUN_TIME
from utils.logger import get_logger

logger = get_logger("Scheduler")


def run_pipeline():
    logger.info(f"Scheduled run triggered at {datetime.now()}")
    try:
        from agents.orchestrator import TradingOrchestrator
        orch = TradingOrchestrator()
        report = orch.run_daily_pipeline()
        if report:
            logger.info(f"Completed. Report: {report.report_txt_path}")
        else:
            logger.info("No report generated (market closed or no candidates).")
    except Exception as e:
        logger.error(f"Pipeline error: {e}", exc_info=True)


def start_scheduler():
    logger.info(f"Scheduler started. Will run weekdays at {RUN_TIME} (local time).")
    schedule.every().monday.at(RUN_TIME).do(run_pipeline)
    schedule.every().tuesday.at(RUN_TIME).do(run_pipeline)
    schedule.every().wednesday.at(RUN_TIME).do(run_pipeline)
    schedule.every().thursday.at(RUN_TIME).do(run_pipeline)
    schedule.every().friday.at(RUN_TIME).do(run_pipeline)

    while True:
        schedule.run_pending()
        time.sleep(30)
