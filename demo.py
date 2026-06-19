import sys
import os
import time

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


def demo_governance():
    logger.info("=" * 70)
    logger.info("  演示: 版本治理校验 — 拦截重复版本号、版本号非递增、并发发布")
    logger.info("=" * 70)
    db.init_db()

    version = "v1.0.0-govtest"
    release_id = db.insert_release(
        version=version, risk_level="normal",
        description="治理测试-第一次提交",
        submitter="开发-小陈", stable_version="v0.9.0",
    )
    db.update_release_status(release_id, "released")
    logger.info(f"  先创建一条已发布记录: #{release_id} {version}")

    passed, violations = governance.validate_release(version=version, stable_version="v0.9.0")
    logger.info(f"\n  场景1: 重复版本号提交 '{version}'")
    if not passed:
        for v in violations:
            logger.info(f"    ✗ {v}")

    passed2, v2 = governance.validate_release(version="v0.8.0", stable_version="v0.9.0")
    logger.info(f"\n  场景2: 版本号 v0.8.0 低于已发布版本 v1.0.0")
    if not passed2:
        for v in v2:
            logger.info(f"    ✗ {v}")

    r2 = db.insert_release(version="v1.1.0-wip", risk_level="normal",
                           description="进行中的发布", submitter="开发-小周",
                           stable_version="v1.0.0-govtest")
    db.update_release_status(r2, "grayscale")
    gs = grayscale.start_next_stage(r2)
    logger.info(f"\n  场景3: 有未完成的同中心发布 v1.1.0-wip (grayscale)")
    passed3, v3 = governance.validate_release(version="v1.2.0", stable_version="v1.0.0-govtest")
    if not passed3:
        for v in v3:
            logger.info(f"    ✗ {v}")

    logger.info(f"\n  场景4: 合法版本 v1.2.0 (无并发时)")
    db.update_release_status(r2, "released")
    if gs:
        grayscale.complete_current_stage(r2)
        for _ in range(3):
            s = grayscale.start_next_stage(r2)
            if not s:
                break
            grayscale.complete_current_stage(r2)
    passed4, v4 = governance.validate_release(version="v1.2.0", stable_version="v1.0.0-govtest")
    if passed4:
        logger.info(f"    ✓ 版本治理校验通过")
    else:
        for v in v4:
            logger.info(f"    ✗ {v}")


def demo_full_flow():
    logger.info("\n" + "=" * 70)
    logger.info("  演示: 完整发布流程 (含加急审批、审批耗时统计)")
    logger.info("=" * 70)

    version = "v2.6.0-" + time.strftime("%H%M%S")
    stable = "v2.5.0"
    release_id = db.insert_release(
        version=version, risk_level="normal",
        description="优化分拣路径算法，加急发布",
        submitter="开发-小刘", stable_version=stable,
        emergency_urgent=True,
    )
    logger.info(f"  已创建发布 #{release_id}, 版本={version}, 加急=是")

    passed, results = precheck.run_prechecks(release_id)
    for r in results:
        mark = "✓" if r["passed"] else "✗"
        logger.info(f"  [{mark}] {r['detail']}")
    if not passed:
        logger.error("前置检查未通过，流程终止")
        return

    roles = approval.init_approval_flow(release_id, emergency_urgent=True)
    logger.info(f"  审批链: {' → '.join(roles)}, 加急模式")

    for role in roles:
        approver = {"tech_lead": "张工", "ops_lead": "李经理", "hub_manager": "王总监"}[role]
        time.sleep(0.3)
        ok, msg = approval.do_approve(release_id, role, approver, "同意发布")
        logger.info(f"  [{role}] {approver} 审批 -> {msg}")

    status = approval.get_approval_status(release_id)
    logger.info(f"\n  审批状态汇总:")
    logger.info(f"    超时阈值: {status['timeout_threshold_min']}分钟")
    for a in status["approvals"]:
        dur = a["duration_str"]
        logger.info(f"    [{a['status']}] {a['role_name']}({a['role']}): 耗时={dur}")

    logger.info("\n  灰度发布 (4阶段逐步推送，第3阶段注入异常触发回滚)...")
    for i in range(4):
        stage = grayscale.start_next_stage(release_id)
        if not stage:
            break
        logger.info(f"  [阶段 {i+1}/4] {stage['stage_name']:12} 覆盖 {len(stage['center_ids'])} 个中心")
        time.sleep(0.3)

        if i == 2:
            logger.warning(f"  !!! 注入异常到 {DISTRIBUTION_CENTERS[1]['id']} !!!")
            sample = monitor.sample_release_once(release_id,
                                                  inject_anomaly_center=DISTRIBUTION_CENTERS[1]["id"])
            if sample.get("anomalies"):
                rb = monitor.trigger_rollback(release_id, sample["anomalies"])
                logger.info(f"\n{rb['report']}\n")
                return
        grayscale.complete_current_stage(release_id)

    logger.info("  全量发布完成")


def demo_drill():
    logger.info("\n" + "=" * 70)
    logger.info("  演示: 回滚演练")
    logger.info("=" * 70)
    result = drill.create_drill("例行回滚演练", "v2.6.0")
    logger.info(f"已创建演练 #{result['drill_id']}")
    plan = result["plan"]
    logger.info(f"涉及分拨: {plan['drill_centers']}, 预计时长: {plan['expected_duration_minutes']}分钟")
    run_result = drill.run_drill(result["drill_id"], simulated=True)
    logger.info(f"\n{run_result['result']}")


def demo_scheduler():
    logger.info("\n" + "=" * 70)
    logger.info("  演示: 后台调度器管理")
    logger.info("=" * 70)

    status = scheduler.get_scheduler_status()
    logger.info(f"  调度器运行中: {'是' if status['running'] else '否'}")
    logger.info(f"  下次周报生成时间: {status.get('next_report_time', 'N/A')}")

    if not status["running"]:
        logger.info("\n  启动调度器...")
        scheduler.start_scheduler(block=False)
        time.sleep(1)

        status2 = scheduler.get_scheduler_status()
        logger.info(f"  调度器运行中: {'是' if status2['running'] else '否'}")
        logger.info(f"  下次周报生成时间: {status2.get('next_report_time', 'N/A')}")
        logger.info(f"  已注册任务: {len(status2.get('jobs', []))} 个")
        for j in status2.get("jobs", []):
            logger.info(f"    {j['job']} | 下次执行: {j['next_run']}")

        logger.info("\n  停止调度器...")
        scheduler.stop_scheduler()
        status3 = scheduler.get_scheduler_status()
        logger.info(f"  调度器运行中: {'是' if status3['running'] else '否'}")


def demo_weekly_report():
    logger.info("\n" + "=" * 70)
    logger.info("  演示: 周统计报表 (PDF+Excel+趋势图表，含各状态/风险/角色耗时)")
    logger.info("=" * 70)

    result = report.generate_weekly_report()
    s = result["stats"]
    pw = s["prev_week"]
    logger.info(f"周期: {s['week_start']} ~ {s['week_end']}")
    logger.info(f"发布总数: {s['release_total']} (上周{pw['total']})")
    logger.info(f"成功发布: {s['release_success']} (上周{pw['success']})")
    logger.info(f"成功率: {s['release_success_rate']*100:.2f}% (上周{pw['success_rate']*100:.2f}%)")
    logger.info(f"回滚次数: {s['rollback_count']}, 失败/驳回: {s['release_failed']}, 进行中: {s['release_in_progress']}")
    logger.info(f"\n-- 各状态分布 --")
    for status, count in s["status_counts"].items():
        if count > 0:
            logger.info(f"  {RELEASE_STATUS_LABELS.get(status, status)}: {count}")
    logger.info(f"\n-- 风险级别 --")
    for risk, count in s["risk_counts"].items():
        logger.info(f"  {risk}: {count}")
    if s["per_role_avg"]:
        from src.config import STAKEHOLDERS
        logger.info(f"\n-- 各角色审批耗时 --")
        for role, avg_s in s["per_role_avg"].items():
            name = STAKEHOLDERS.get(role, {}).get("name", role)
            logger.info(f"  {name}({role}): {avg_s/60:.1f} 分钟")
    if result["pdf"]:
        logger.info(f"\nPDF 报表: {result['pdf']}")
    if result["excel"]:
        logger.info(f"Excel 报表: {result['excel']}")
    if result["chart"]:
        logger.info(f"趋势图表: {result['chart']}")


def demo_history_query():
    logger.info("\n" + "=" * 70)
    logger.info("  演示: 历史记录查询与导出 (含审批耗时/超时提醒/加急标记)")
    logger.info("=" * 70)

    results = history.search_releases(risk_level="normal")
    logger.info(f"找到 {len(results)} 条常规发布记录")
    for r in results[:5]:
        urgent_tag = " [加急]" if r.get("emergency_urgent") else ""
        logger.info(f"  #{r['id']} {r['version']} ({r['status']}){urgent_tag} by {r['submitter']}")

    if results:
        detail = history.get_release_detail(results[0]["id"])
        logger.info(f"\n  发布 #{results[0]['id']} 详情:")
        logger.info(f"    前置检查: {len(detail['prechecks'])} 项")
        logger.info(f"    审批记录: {len(detail['approvals'])} 条")
        for a in detail["approvals"]:
            dur = a.get("duration_str", "-")
            reminded = " [超时已提醒]" if a.get("timeout_reminded") else ""
            logger.info(f"      [{a['status']}] {a['role']}: 耗时={dur}{reminded}")
        logger.info(f"    灰度阶段: {len(detail['grayscale_stages'])} 个")
        logger.info(f"    回滚记录: {len(detail['rollbacks'])} 条")

    logger.info("\n导出 Excel (含审批记录/回滚记录工作表)...")
    path = history.export_releases(results, fmt="excel", filename_prefix="demo_export")
    if path:
        logger.info(f"导出文件: {path}")


def main():
    try:
        demo_governance()
        time.sleep(0.5)
        demo_full_flow()
        time.sleep(0.5)
        demo_drill()
        time.sleep(0.5)
        demo_scheduler()
        time.sleep(0.5)
        demo_weekly_report()
        time.sleep(0.5)
        demo_history_query()

        logger.info("\n" + "=" * 70)
        logger.info("  ✅ 所有演示完成！")
        logger.info("  数据库: data/release_system.db")
        logger.info("  操作日志: logs/YYYYMMDD.log")
        logger.info("  报表文件: reports/")
        logger.info("  命令行用法: python main.py <command> [options]")
        logger.info("    scheduler start/stop/status  - 调度器管理")
        logger.info("    submit --version V --submitter S --stable-version V --urgent")
        logger.info("    approval status --release-id N")
        logger.info("    report generate")
        logger.info("    history search/detail/export")
        logger.info("=" * 70)
    except KeyboardInterrupt:
        logger.info("\n用户中断")


if __name__ == "__main__":
    main()
