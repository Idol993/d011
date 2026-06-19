import time
import threading
import schedule
from datetime import datetime
from .logger import get_logger
from . import report
from . import database as db

logger = get_logger("scheduler")


def _weekly_report_job():
    logger.info("触发每周一自动生成周报任务")
    try:
        db.init_db()
        result = report.generate_weekly_report()
        logger.info(f"周报自动生成完成: report_id={result['report_id']}, "
                    f"PDF={result.get('pdf')}, Excel={result.get('excel')}")
    except Exception as e:
        logger.error(f"自动生成周报失败: {e}")


def start_scheduler():
    schedule.every().monday.at("09:00").do(_weekly_report_job)
    logger.info("定时调度器已启动，每周一 09:00 自动生成周报")

    def run():
        while True:
            try:
                schedule.run_pending()
            except Exception as e:
                logger.error(f"调度器运行异常: {e}")
            time.sleep(30)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def run_now():
    _weekly_report_job()
