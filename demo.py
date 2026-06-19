import sys
import os
import time
import io
from datetime import datetime, timedelta

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'buffer'):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.logger import get_logger
from src import database as db
from src import precheck
from src import approval
from src import grayscale
from src import monitor
from src import drill
from src import report
from src import history
from src import governance
from src import scheduler
from src.config import DISTRIBUTION_CENTERS, RELEASE_STATUS_LABELS

logger = get_logger("DEMO")


def demo_governance_parallel():
    logger.info("=" * 70)
    logger.info("  演示1: 版本治理 - 并行发布按中心交集判断")
    logger.info("=" * 70)
    db.init_db()

    safe_window = datetime(2026, 7, 10, 14, 30, 0)

    baseline = db.insert_release(
        version="v6.0.0-stable", risk_level="normal",
        description="稳定基线", submitter="发布管理员",
    )
    db.update_release_status(baseline, "released")
    logger.info(f"先造一条已发布基线: v6.0.0-stable (ID={baseline})")

    r1 = db.insert_release(
        version="v6.1.0-dc01", risk_level="normal",
        description="华北地区专属", submitter="开发A",
        target_center_ids=["DC001", "DC012"],
    )
    db.update_release_status(r1, "grayscale")
    grayscale.start_next_stage(r1)
    logger.info(f"活跃发布 v6.1.0-dc01: 目标中心=['DC001','DC012'] 灰度中")

    passed, violations, extra = governance.validate_release(
        "v6.1.0-dc09", risk_level="normal",
        target_center_ids=["DC009", "DC010"],
        now=safe_window,
    )
    logger.info(f"\n提交 v6.1.0-dc09: 目标中心=['DC009','DC010'] -> 通过={passed}")
    if not passed:
        for v in violations:
            logger.warning(f"  {v}")
    else:
        logger.info(f"  OK 无中心重叠，放行")

    passed2, violations2, _ = governance.validate_release(
        "v6.1.0-dc01-conflict", risk_level="normal",
        target_center_ids=["DC001", "DC005"],
        now=safe_window,
    )
    logger.info(f"\n提交 v6.1.0-dc01-conflict: 目标中心=['DC001','DC005'] -> 通过={passed2}")
    for v in violations2:
        logger.warning(f"  {v}")


def demo_governance_window():
    logger.info("\n" + "=" * 70)
    logger.info("  演示2: 发布窗口冻结期 - 普通发布拦截 / 紧急发布绕过留痕")
    logger.info("=" * 70)
    safe_window = datetime(2026, 7, 10, 14, 30, 0)
    peak = safe_window.replace(hour=3, minute=0)

    passed, violations, bypassed = governance.check_release_windows(
        risk_level="normal", target_center_ids=["DC001"], now=peak,
    )
    logger.info(f"模拟 3:00 普通发布 DC001 -> 通过={passed}")
    for v in violations:
        logger.warning(f"  {v}")

    passed2, violations2, bypassed2 = governance.check_release_windows(
        risk_level="emergency", target_center_ids=["DC001"], now=peak,
    )
    logger.info(f"模拟 3:00 紧急发布 DC001 -> 通过={passed2}, 绕过窗口数={len(bypassed2)}")
    for b in bypassed2:
        logger.info(f"  已绕过: {b['name']} {b.get('detail','')} - {b['reason']}")

    release_id = db.insert_release(
        version="v9.9.9-demo-emergency",
        risk_level="emergency",
        description="演示紧急发布绕过冻结窗口",
        submitter="紧急值班",
        emergency_urgent=True,
        governance_bypassed=bypassed2,
        target_center_ids=["DC001"],
    )
    detail = db.get_release(release_id)
    logger.info(f"\n紧急发布 #{release_id} 创建成功")
    bypassed_saved = detail.get("governance_bypassed") or []
    if bypassed_saved:
        logger.info(f"DB 持久化冻结窗口绕过记录: {bypassed_saved[0]['name']}")


def demo_scheduler_persistence():
    logger.info("\n" + "=" * 70)
    logger.info("  演示3: 调度器状态 - 持久化任务记录 (重启可见)")
    logger.info("=" * 70)

    r1 = db.insert_scheduler_run(
        "_weekly_report_job",
        started_at=(datetime.now() - timedelta(days=1)).isoformat(timespec="seconds"),
    )
    db.complete_scheduler_run(
        r1, "success",
        result="report_id=1, success_rate=95.0%, total=20, rollback=1",
    )

    r2 = db.insert_scheduler_run(
        "_approval_timeout_check",
        started_at=(datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds"),
    )
    db.complete_scheduler_run(r2, "success", result="reminded=3")

    r3 = db.insert_scheduler_run(
        "_weekly_report_job",
        started_at=(datetime.now() - timedelta(hours=1)).isoformat(timespec="seconds"),
    )
    db.complete_scheduler_run(
        r3, "failed",
        error_detail="disk full /reports 目录不可写",
    )
    logger.info("插入 3 条调度器历史执行记录")

    status = scheduler.get_scheduler_status()
    logger.info(f"\n调度器运行中: {'是' if status['running'] else '否'}")
    logger.info(f"下次周报生成: {status.get('next_report_time', 'N/A')}")

    logger.info("\n最近任务执行记录 (持久化, 重启可见):")
    label_map = {
        "_weekly_report_job": "周报生成",
        "_approval_timeout_check": "审批超时巡检",
    }
    for j, info in status.get("last_runs", {}).items():
        label = label_map.get(j, j)
        st = info.get("status", "?")
        mark = "OK" if st == "success" else ("..." if st == "running" else "FAIL")
        logger.info(f"  [{mark}] {label}: 状态={st}")
        if st != "never":
            logger.info(f"      开始: {info.get('started_at')}, 耗时: {info.get('duration_seconds', '-')}s")
            if info.get("result"):
                logger.info(f"      结果: {info['result']}")
            if info.get("error_detail"):
                logger.error(f"      失败原因: {info['error_detail']}")

    recent = status.get("recent_runs_sample", [])
    logger.info(f"\n近{len(recent)}次调度历史抽样 (共{status.get('recent_runs_count',0)}条):")
    for r in recent:
        err = r.get("error") or "-"
        dur = f"{r['duration_seconds']:.1f}s" if r.get("duration_seconds") else "-"
        logger.info(f"  #{r['id']} {r['job_name']:<26} [{r['status']:<8}] "
                    f"@{r['started_at']} dur={dur} err={err}")


def demo_weekly_json():
    logger.info("\n" + "=" * 70)
    logger.info("  演示4: 周报 JSON 汇总 - 管理层看板数据")
    logger.info("=" * 70)

    result = report.generate_weekly_report()
    s = result["stats"]
    analytics = s["json_analytics"]

    logger.info(f"JSON路径: {result.get('json')}")
    logger.info(f"\n核心指标:")
    cm = analytics["core_metrics"]
    logger.info(f"  发布总数: {cm['release_total']}, 成功: {cm['release_success']}, "
                f"成功率: {cm['release_success_rate']*100:.2f}%")
    logger.info(f"  失败: {cm['release_failed']}, 进行中: {cm['release_in_progress']}, "
                f"回滚: {cm['rollback_count']}")
    logger.info(f"  平均审批时长: {cm['avg_approval_minutes']:.2f} 分钟")

    logger.info(f"\n风险排行:")
    for r in analytics["risk_ranking"]:
        logger.info(f"  {r['risk_level']:<10} 总数={r['total']:<4} "
                    f"成功率={r['success_rate']*100:.2f}% 回滚={r['rollback_count']}")

    logger.info(f"\n回滚原因 Top:")
    if analytics["rollback_reason_top"]:
        for r in analytics["rollback_reason_top"]:
            logger.info(f"  {r['count']:<4} {r['reason']}")
    else:
        logger.info("  (本周无回滚)")

    logger.info(f"\n审批超时 Top:")
    if analytics["approval_timeout_top"]:
        for r in analytics["approval_timeout_top"]:
            logger.info(f"  {r['version']:<18} {r['role']:<14} "
                        f"{r['elapsed_minutes']:.1f}min (阈值{r['threshold_minutes']}min)")
    else:
        logger.info("  (本周无审批超时)")

    logger.info(f"\n分拨中心维度成功率 (Top 5):")
    for r in analytics["center_success_rates"][:5]:
        logger.info(f"  {r['center_id']} {r['center_name']:<14} "
                    f"总数={r['release_total']:<3} 成功率={r['success_rate']*100:.1f}%")

    delta = analytics["weekly_comparison"]["delta"]
    logger.info(f"\n周环比变化:")
    logger.info(f"  成功率变化: {delta['success_rate_delta_pct']:+.2f} 百分点")
    logger.info(f"  发布数变化: {delta['total_delta']:+d}, 回滚数变化: {delta['rollback_delta']:+d}")


def main():
    try:
        demo_governance_parallel()
        demo_governance_window()
        demo_scheduler_persistence()
        demo_weekly_json()

        logger.info("\n" + "=" * 70)
        logger.info("  所有演示完成！")
        logger.info("  CLI 常用命令:")
        logger.info("    python main.py submit --version V --submitter S --target-centers DC001,DC002")
        logger.info("    python main.py scheduler start/stop/status")
        logger.info("    python main.py report generate   (PDF + Excel + JSON)")
        logger.info("    python main.py history detail --release-id N")
        logger.info("=" * 70)
    except KeyboardInterrupt:
        logger.info("\n用户中断")


if __name__ == "__main__":
    main()
