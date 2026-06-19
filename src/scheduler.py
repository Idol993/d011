import os
import sys
import time
import subprocess
import signal
import threading
import schedule
from datetime import datetime, timedelta
from typing import Dict, Optional, List
from .config import (SCHEDULER_PID_PATH, SCHEDULER_LOCK_PATH,
                     APPROVAL_TIMEOUT_MINUTES, APPROVAL_TIMEOUT_CHECK_INTERVAL,
                     WEEKLY_REPORT_DAY, WEEKLY_REPORT_TIME,
                     GRAYSCALE_STAGES)
from .logger import get_logger
from . import report
from . import database as db
from . import notifier
from . import grayscale as gs

logger = get_logger("scheduler")

_scheduler_thread = None
_running = False


def _write_pid(pid: int = None):
    with open(SCHEDULER_PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(pid or os.getpid()))


def _remove_pid():
    try:
        os.remove(SCHEDULER_PID_PATH)
    except FileNotFoundError:
        pass


def _read_pid() -> Optional[int]:
    if not os.path.exists(SCHEDULER_PID_PATH):
        return None
    try:
        with open(SCHEDULER_PID_PATH, "r") as f:
            return int(f.read().strip())
    except Exception:
        return None


def is_scheduler_running() -> bool:
    pid = _read_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        _remove_pid()
        return False


def _weekly_report_job():
    logger.info("触发每周一自动生成周报任务")
    run_id = db.insert_scheduler_run("_weekly_report_job")
    status = "success"
    result_text = None
    err_text = None
    try:
        db.init_db()
        result = report.generate_weekly_report()
        result_text = (
            f"report_id={result['report_id']}, "
            f"success_rate={result['stats']['release_success_rate']*100:.2f}%, "
            f"total={result['stats']['release_total']}, "
            f"rollback={result['stats']['rollback_count']}"
        )
        logger.info(f"周报自动生成完成: {result_text}")
    except Exception as e:
        status = "failed"
        err_text = str(e)
        logger.error(f"自动生成周报失败: {e}")
    finally:
        try:
            db.complete_scheduler_run(run_id, status, result=result_text, error_detail=err_text)
        except Exception as ex:
            logger.error(f"写入调度器执行记录失败: {ex}")


def _approval_timeout_check():
    run_id = db.insert_scheduler_run("_approval_timeout_check")
    status = "success"
    result_text = None
    err_text = None
    reminded_count = 0
    try:
        db.init_db()
        for risk_level, timeout_min in APPROVAL_TIMEOUT_MINUTES.items():
            timed_out = db.get_timed_out_approvals(timeout_min)
            for a in timed_out:
                if a["risk_level"] != risk_level:
                    continue
                version = a.get("version", "?")
                role = a["role"]
                elapsed = (datetime.now() - datetime.fromisoformat(a["created_at"])).total_seconds() / 60
                logger.warning(f"审批超时提醒: 版本={version}, 角色={role}, "
                               f"已等待{elapsed:.0f}分钟(阈值{timeout_min}分钟)")
                notifier.notify_approval_timeout(version, role, elapsed, timeout_min)
                db.mark_timeout_reminded(a["id"])
                reminded_count += 1
        result_text = f"reminded={reminded_count}"
    except Exception as e:
        status = "failed"
        err_text = str(e)
        logger.error(f"审批超时巡检异常: {e}")
    finally:
        try:
            db.complete_scheduler_run(run_id, status, result=result_text, error_detail=err_text)
        except Exception as ex:
            logger.error(f"写入调度器执行记录失败: {ex}")


def _grayscale_progress_job():
    """检查灰度阶段是否超过 wait_hours，推进到下一阶段，跳过 paused 发布"""
    run_id = db.insert_scheduler_run("_grayscale_progress_job")
    run_status = "success"
    result_text = None
    err_text = None
    promoted = 0
    skipped_paused = 0
    try:
        db.init_db()
        releases = db.list_releases({"status": "grayscale"})
        paused_releases = db.list_releases({"status": "paused"})
        skipped_paused = len(paused_releases)
        for rel in releases:
            try:
                current = db.get_current_stage(rel["id"])
                if not current:
                    continue
                started_at = current.get("started_at")
                if not started_at:
                    continue
                stage_def = next(
                    (s for s in GRAYSCALE_STAGES if s["name"] == current["stage_name"]),
                    None,
                )
                if not stage_def:
                    continue
                wait_hours = stage_def["wait_hours"]
                if wait_hours <= 0:
                    continue
                try:
                    dt_started = datetime.fromisoformat(started_at)
                except Exception:
                    continue
                elapsed_hours = (datetime.now() - dt_started).total_seconds() / 3600.0
                if elapsed_hours < wait_hours:
                    continue
                logger.info(
                    f"版本 {rel['version']} 阶段 {current['stage_name']} "
                    f"已等待 {elapsed_hours:.1f}h >= {wait_hours}h，自动推进下一阶段"
                )
                gs.complete_current_stage(rel["id"])
                gs.start_next_stage(rel["id"])
                promoted += 1
            except Exception as ex:
                logger.error(f"自动推进灰度失败 release={rel['id']}: {ex}")
        result_text = f"promoted={promoted}, skipped_paused={skipped_paused}"
    except Exception as e:
        run_status = "failed"
        err_text = str(e)
        logger.error(f"灰度推进巡检异常: {e}")
    finally:
        try:
            db.complete_scheduler_run(run_id, run_status, result=result_text, error_detail=err_text)
        except Exception as ex:
            logger.error(f"写入调度器执行记录失败: {ex}")


def get_next_report_time() -> Optional[datetime]:
    now = datetime.now()
    day_map = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    target_day = day_map.get(WEEKLY_REPORT_DAY, 0)
    hour, minute = map(int, WEEKLY_REPORT_TIME.split(":"))
    days_ahead = (target_day - now.weekday()) % 7
    if days_ahead == 0:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            days_ahead = 7
    next_time = now + timedelta(days=days_ahead)
    next_time = next_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return next_time


def _run_foreground_loop():
    global _running
    _running = True
    _write_pid()

    getattr(schedule.every(), WEEKLY_REPORT_DAY).at(WEEKLY_REPORT_TIME).do(_weekly_report_job)
    schedule.every(APPROVAL_TIMEOUT_CHECK_INTERVAL).seconds.do(_approval_timeout_check)
    schedule.every(10).minutes.do(_grayscale_progress_job)

    next_time = get_next_report_time()
    logger.info(f"调度器已启动 PID={os.getpid()}, "
                f"下次周报生成: {next_time.isoformat(timespec='seconds') if next_time else 'N/A'}, "
                f"审批超时巡检间隔: {APPROVAL_TIMEOUT_CHECK_INTERVAL}s")

    try:
        while _running:
            try:
                schedule.run_pending()
            except Exception as e:
                logger.error(f"调度器运行异常: {e}")
            time.sleep(15)
    except KeyboardInterrupt:
        pass
    finally:
        _running = False
        _remove_pid()
        schedule.clear()
        logger.info("调度器已停止")


def start_scheduler(block: bool = False):
    if is_scheduler_running():
        logger.warning("调度器已在运行 (PID文件存在)")
        return None

    if block:
        db.init_db()
        _run_foreground_loop()
        return None

    main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    python_exe = sys.executable

    proc = subprocess.Popen(
        [python_exe, main_py, "_scheduler_worker"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if sys.platform == "win32" else 0,
    )
    time.sleep(0.5)

    if proc.poll() is not None:
        logger.error("调度器后台进程启动失败")
        return None

    _write_pid(proc.pid)
    next_time = get_next_report_time()
    logger.info(f"调度器已启动 PID={proc.pid}, "
                f"下次周报生成: {next_time.isoformat(timespec='seconds') if next_time else 'N/A'}")
    return proc


def stop_scheduler():
    pid = _read_pid()
    if pid is not None:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info(f"已发送停止信号到 PID={pid}")
        except (OSError, ProcessLookupError):
            logger.warning(f"PID={pid} 进程不存在")
    _remove_pid()
    schedule.clear()
    logger.info("调度器已停止")


def get_scheduler_status() -> Dict:
    running = is_scheduler_running()
    pid = _read_pid()
    next_time = get_next_report_time()
    jobs = []
    for job in schedule.get_jobs():
        jobs.append({
            "job": str(job.job_func.__name__) if job.job_func else "?",
            "next_run": str(job.next_run) if job.next_run else "?",
            "interval": str(job.interval) if hasattr(job, "interval") else "?",
        })

    recent_runs = db.list_scheduler_runs(limit=20) if hasattr(db, "list_scheduler_runs") else []

    known_jobs = ["_weekly_report_job", "_approval_timeout_check", "_grayscale_progress_job"]
    last_run_by_job = {}
    for j in known_jobs:
        last = db.get_last_scheduler_run(j) if hasattr(db, "get_last_scheduler_run") else None
        if last:
            last_run_by_job[j] = {
                "status": last["status"],
                "started_at": last["started_at"],
                "finished_at": last.get("finished_at"),
                "duration_seconds": last.get("duration_seconds"),
                "result": last.get("result"),
                "error_detail": last.get("error_detail"),
            }
        else:
            last_run_by_job[j] = {"status": "never"}

    return {
        "running": running,
        "pid": pid,
        "next_report_time": next_time.isoformat(timespec="seconds") if next_time else None,
        "jobs": jobs,
        "approval_timeouts": APPROVAL_TIMEOUT_MINUTES,
        "last_runs": last_run_by_job,
        "recent_runs_count": len(recent_runs),
        "recent_runs_sample": [
            {"id": r["id"], "job_name": r["job_name"], "status": r["status"],
             "started_at": r["started_at"], "duration_seconds": r.get("duration_seconds"),
             "error": r.get("error_detail")}
            for r in recent_runs[:5]
        ],
    }
