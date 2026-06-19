import random
import time
from datetime import datetime
from typing import Dict, List, Optional
from .config import DISTRIBUTION_CENTERS
from .logger import get_logger
from . import database as db
from . import notifier

logger = get_logger("drill")


def generate_drill_plan(target_version: Optional[str] = None) -> Dict:
    drill_centers = random.sample([c["id"] for c in DISTRIBUTION_CENTERS],
                                  k=min(3, len(DISTRIBUTION_CENTERS)))
    steps = [
        {"step": 1, "action": "确认演练窗口与干系人通知", "status": "pending"},
        {"step": 2, "action": f"冻结版本 {target_version or '指定版本'} 发布队列", "status": "pending"},
        {"step": 3, "action": "记录分拨中心当前版本状态", "status": "pending"},
        {"step": 4, "action": f"模拟触发异常指标 (中心: {', '.join(drill_centers)})", "status": "pending"},
        {"step": 5, "action": "验证监控告警触发", "status": "pending"},
        {"step": 6, "action": "执行自动化回滚脚本", "status": "pending"},
        {"step": 7, "action": f"验证分拨中心 {', '.join(drill_centers)} 版本恢复", "status": "pending"},
        {"step": 8, "action": "验证业务指标回归正常", "status": "pending"},
        {"step": 9, "action": "生成演练报告与复盘记录", "status": "pending"},
    ]
    return {
        "drill_centers": drill_centers,
        "target_version": target_version,
        "expected_duration_minutes": 30,
        "steps": steps,
        "checklist": [
            "回滚脚本可执行",
            "稳定版本备份可用",
            "分拨中心网络连通正常",
            "巴枪设备可回退版本",
            "监控告警通道正常",
        ],
    }


def create_drill(drill_name: str, target_version: Optional[str] = None) -> Dict:
    plan = generate_drill_plan(target_version)
    drill_id = db.insert_drill(drill_name, target_version, plan)
    logger.info(f"已创建回滚演练: {drill_name}(#{drill_id}), 目标版本={target_version}")
    return {"drill_id": drill_id, "plan": plan}


def run_drill(drill_id: int, simulated: bool = True) -> Dict:
    drills = db.list_drills()
    drill = next((d for d in drills if d["id"] == drill_id), None)
    if not drill:
        return {"error": f"演练 {drill_id} 不存在"}

    db.update_drill_status(drill_id, "running")
    logger.info(f"开始执行演练: {drill['drill_name']}(#{drill_id})")

    plan = drill["plan"]
    steps = plan.get("steps", [])
    step_logs = []
    all_passed = True

    for step in steps:
        time.sleep(0.3)
        passed = simulated or random.random() > 0.15
        step["status"] = "passed" if passed else "failed"
        log_msg = f"步骤{step['step']}: {step['action']} -> {'通过' if passed else '失败'}"
        step_logs.append(log_msg)
        logger.info(f"[演练#{drill_id}] {log_msg}")
        if not passed:
            all_passed = False

    result_lines = [
        f"演练名称: {drill['drill_name']}",
        f"演练ID: {drill_id}",
        f"目标版本: {drill['target_version']}",
        f"演练时间: {datetime.now().isoformat(timespec='seconds')}",
        f"演练分拨中心: {', '.join(plan.get('drill_centers', []))}",
        "",
        "--- 执行步骤 ---",
    ] + step_logs + [
        "",
        "--- 检查项验证 ---",
    ]
    for item in plan.get("checklist", []):
        ok = simulated or random.random() > 0.1
        result_lines.append(f"  [{'✓' if ok else '✗'}] {item}")
        if not ok:
            all_passed = False

    status = "passed" if all_passed else "failed"
    result_lines.append("")
    result_lines.append(f"演练最终结果: {status.upper()}")
    result_text = "\n".join(result_lines)

    db.update_drill_status(drill_id, "completed", result_text)
    notifier.notify_drill_completed(drill["drill_name"], status, result_text)
    logger.info(f"演练#{drill_id} 完成, 结果={status}")

    return {
        "drill_id": drill_id,
        "status": status,
        "result": result_text,
        "updated_plan": plan,
    }
