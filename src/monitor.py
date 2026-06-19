import random
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .config import MONITOR_THRESHOLDS
from .logger import get_logger
from . import database as db
from . import notifier
from . import grayscale as gs

logger = get_logger("monitor")


def _generate_metrics(center_id: str, has_anomaly: bool = False) -> Tuple[float, float, float]:
    if has_anomaly:
        fail = round(random.uniform(0.035, 0.08), 4)
        delay = round(random.uniform(130, 300), 2)
        loss = round(random.uniform(0.006, 0.02), 4)
    else:
        fail = round(random.uniform(0.001, 0.02), 4)
        delay = round(random.uniform(20, 100), 2)
        loss = round(random.uniform(0.0001, 0.003), 4)
    return fail, delay, loss


def _check_anomaly(scan_fail_rate: float, sort_delay: float,
                   loss_rate: float) -> Tuple[bool, str]:
    reasons = []
    if scan_fail_rate > MONITOR_THRESHOLDS["scan_fail_rate_max"]:
        reasons.append(f"扫描失败率 {scan_fail_rate*100:.2f}% > 阈值 {MONITOR_THRESHOLDS['scan_fail_rate_max']*100:.2f}%")
    if sort_delay > MONITOR_THRESHOLDS["sort_delay_max"]:
        reasons.append(f"分拣延迟 {sort_delay:.1f}s > 阈值 {MONITOR_THRESHOLDS['sort_delay_max']}s")
    if loss_rate > MONITOR_THRESHOLDS["loss_rate_max"]:
        reasons.append(f"丢件异常率 {loss_rate*100:.3f}% > 阈值 {MONITOR_THRESHOLDS['loss_rate_max']*100:.3f}%")
    return (len(reasons) > 0, "; ".join(reasons))


def sample_release_once(release_id: int, inject_anomaly_center: Optional[str] = None) -> Dict:
    release = db.get_release(release_id)
    if not release:
        return {}

    current_stage = db.get_current_stage(release_id)
    if not current_stage:
        logger.info(f"版本 {release['version']} 无运行中灰度阶段，跳过监控采样")
        return {}

    stage_id = current_stage["id"]
    centers = current_stage["center_ids"]
    result = {"anomalies": [], "records": []}

    for center_id in centers:
        inject = (inject_anomaly_center == center_id)
        fail, delay, loss = _generate_metrics(center_id, has_anomaly=inject)
        is_anomaly, reason = _check_anomaly(fail, delay, loss)
        rec_id = db.insert_monitor_record(
            release_id, stage_id, center_id, fail, delay, loss, is_anomaly
        )
        rec = {
            "id": rec_id,
            "center_id": center_id,
            "scan_fail_rate": fail,
            "sort_delay": delay,
            "loss_rate": loss,
            "is_anomaly": is_anomaly,
            "reason": reason,
        }
        result["records"].append(rec)
        if is_anomaly:
            result["anomalies"].append(rec)
            notifier.notify_anomaly_detected(
                release["version"], center_id,
                {"scan_fail_rate": fail, "sort_delay": delay, "loss_rate": loss},
                reason,
            )

    return result


def trigger_rollback(release_id: int, anomalies: List[Dict]) -> Dict:
    release = db.get_release(release_id)
    if not release:
        return {}

    current_stage = db.get_current_stage(release_id)
    stage_id = current_stage["id"] if current_stage else None
    affected_centers = list({a["center_id"] for a in anomalies})
    affected_parcels = sum(random.randint(500, 5000) for _ in affected_centers)
    reason = "; ".join({a["reason"] for a in anomalies})
    anomaly_detail = "\n".join(
        [f"[{a['center_id']}] fail={a['scan_fail_rate']*100:.2f}% "
         f"delay={a['sort_delay']:.1f}s loss={a['loss_rate']*100:.3f}%"
         for a in anomalies]
    )
    restored_version = release["stable_version"] or "UNKNOWN_STABLE"

    rollback_id = db.insert_rollback(
        release_id, stage_id, affected_centers, affected_parcels,
        reason, anomaly_detail, restored_version,
    )

    notifier.notify_rollback_started(
        release["version"], rollback_id, affected_centers, affected_parcels, reason
    )
    logger.warning(f"触发自动回滚 rollback_id={rollback_id}, version={release['version']}, "
                   f"centers={affected_centers}")

    time.sleep(1.0)

    db.complete_rollback(rollback_id)
    db.update_release_status(release_id, "rolled_back", rollback_reason=reason)
    if current_stage:
        db.complete_grayscale_stage(current_stage["id"])

    notifier.notify_rollback_completed(release["version"], rollback_id, restored_version)
    logger.info(f"回滚完成 rollback_id={rollback_id}, 恢复至版本 {restored_version}")

    rollback_report = generate_rollback_report(rollback_id)
    logger.info(f"回滚报告已生成:\n{rollback_report}")

    return {
        "rollback_id": rollback_id,
        "restored_version": restored_version,
        "affected_centers": affected_centers,
        "affected_parcels": affected_parcels,
        "reason": reason,
        "report": rollback_report,
    }


def generate_rollback_report(rollback_id: int) -> str:
    rollbacks = db.list_rollbacks()
    rb = next((r for r in rollbacks if r["id"] == rollback_id), None)
    if not rb:
        return ""
    release = db.get_release(rb["release_id"])
    version = release["version"] if release else "UNKNOWN"

    lines = [
        "=" * 60,
        "  快递快运分拨系统 - 自动回滚报告",
        "=" * 60,
        f"回滚ID        : {rb['id']}",
        f"问题版本      : {version}",
        f"恢复版本      : {rb['rolled_back_version']}",
        f"回滚状态      : {rb['status']}",
        f"触发时间      : {rb['created_at']}",
        f"完成时间      : {rb['completed_at']}",
        "",
        "--- 影响范围 ---",
        f"影响分拨中心  : {', '.join(rb['affected_centers'])}",
        f"影响异常件量  : {rb['affected_parcels']} 件",
        "",
        "--- 原因分析 ---",
        f"触发原因      : {rb['reason']}",
        f"详细异常数据  :",
        f"{rb['anomaly_detail']}",
        "",
        "--- 后续处置 ---",
        "  1. 已恢复至上次稳定版本",
        "  2. 已暂停灰度发布流程",
        "  3. 技术团队正在排查根因",
        "  4. 监控流程已重启",
        "=" * 60,
        f"报告生成时间  : {datetime.now().isoformat(timespec='seconds')}",
    ]
    return "\n".join(lines)


_running = False
_thread: Optional[threading.Thread] = None


def _monitor_loop():
    global _running
    logger.info("监控线程启动, 采样间隔="
                f"{MONITOR_THRESHOLDS['monitor_interval_seconds']}s")
    while _running:
        try:
            releases = db.list_releases({"status": "grayscale"})
            for rel in releases:
                sample_result = sample_release_once(rel["id"])
                if sample_result.get("anomalies"):
                    trigger_rollback(rel["id"], sample_result["anomalies"])
        except Exception as e:
            logger.error(f"监控循环异常: {e}")
        for _ in range(MONITOR_THRESHOLDS["monitor_interval_seconds"]):
            if not _running:
                break
            time.sleep(1)
    logger.info("监控线程已停止")


def start_monitoring():
    global _running, _thread
    if _running:
        logger.info("监控已在运行")
        return
    _running = True
    _thread = threading.Thread(target=_monitor_loop, daemon=True)
    _thread.start()


def stop_monitoring():
    global _running
    _running = False
    if _thread:
        _thread.join(timeout=5)
