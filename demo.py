"""
演示脚本：快速展示完整的发布 -> 审批 -> 灰度 -> 异常回滚流程
运行: python demo.py
"""
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
from src.config import DISTRIBUTION_CENTERS

logger = get_logger("DEMO")


def demo_full_flow():
    logger.info("=" * 70)
    logger.info("  演示: 快递快运分拨系统版本发布与智能回滚完整流程")
    logger.info("=" * 70)

    db.init_db()

    logger.info("\n--- 步骤1: 提交发布申请 (常规风险) ---")
    version = "v2.5.0-" + time.strftime("%H%M%S")
    stable = "v2.4.3"
    release_id = db.insert_release(
        version=version,
        risk_level="normal",
        description="优化巴枪扫描算法，提升分拣效率15%",
        submitter="开发-小刘",
        stable_version=stable,
    )
    logger.info(f"  已创建发布 #{release_id}, 版本={version}, 稳定版本={stable}")

    logger.info("\n--- 步骤2: 执行前置条件检查 ---")
    passed, results = precheck.run_prechecks(release_id)
    for r in results:
        mark = "✓" if r["passed"] else "✗"
        logger.info(f"  [{mark}] {r['detail']}")
    logger.info(f"  检查结论: {'通过' if passed else '未通过'}")

    if not passed:
        logger.error("前置检查未通过，流程终止")
        return

    logger.info("\n--- 步骤3: 初始化审批流程 (常规: 技术→运营→分拨负责人) ---")
    roles = approval.init_approval_flow(release_id)
    logger.info(f"  审批链: {' → '.join(roles)}")

    for role in roles:
        approver = {"tech_lead": "张工", "ops_lead": "李经理", "hub_manager": "王总监"}[role]
        ok, msg = approval.do_approve(release_id, role, approver, "同意发布")
        logger.info(f"  [{role}] {approver} 审批 -> {msg}")
        time.sleep(0.5)

    logger.info("\n--- 步骤4: 灰度发布 (4个阶段逐步推送) ---")
    for i in range(4):
        stage = grayscale.start_next_stage(release_id)
        if not stage:
            logger.info("  所有灰度阶段已完成")
            break
        logger.info(f"  [阶段 {i+1}/4] {stage['stage_name']:12} 覆盖 "
                    f"{len(stage['center_ids'])} 个分拨中心: {stage['center_ids']}")
        time.sleep(0.5)

        if i == 2:
            logger.warning(f"  !!! 注入异常到 {DISTRIBUTION_CENTERS[1]['id']} 以演示自动回滚 !!!")
            sample = monitor.sample_release_once(release_id,
                                                  inject_anomaly_center=DISTRIBUTION_CENTERS[1]["id"])
            if sample.get("anomalies"):
                logger.warning(f"  检测到 {len(sample['anomalies'])} 个异常，触发自动回滚...")
                rb_result = monitor.trigger_rollback(release_id, sample["anomalies"])
                logger.info(f"\n{rb_result['report']}\n")
                return
        grayscale.complete_current_stage(release_id)
        logger.info(f"  阶段 {stage['stage_name']} 观察期通过")

    logger.info("\n--- 步骤5: 全量发布完成 ---")


def demo_drill():
    logger.info("\n" + "=" * 70)
    logger.info("  演示: 手动创建回滚演练并执行")
    logger.info("=" * 70)

    result = drill.create_drill("周三例行回滚演练", "v2.5.0")
    logger.info(f"已创建演练 #{result['drill_id']}")
    plan = result["plan"]
    logger.info(f"涉及分拨: {plan['drill_centers']}, 预计时长: {plan['expected_duration_minutes']}分钟")

    logger.info("\n开始执行演练...")
    run_result = drill.run_drill(result["drill_id"], simulated=True)
    logger.info(f"\n{run_result['result']}")


def demo_weekly_report():
    logger.info("\n" + "=" * 70)
    logger.info("  演示: 生成周统计报表 (PDF + Excel + 趋势图表)")
    logger.info("=" * 70)

    result = report.generate_weekly_report()
    s = result["stats"]
    logger.info(f"周期: {s['week_start']} ~ {s['week_end']}")
    logger.info(f"发布总数: {s['release_total']}, 成功: {s['release_success']}, "
                f"成功率: {s['release_success_rate']*100:.2f}%")
    logger.info(f"回滚次数: {s['rollback_count']}")
    logger.info(f"平均审批时长: {s['avg_approval_seconds']/60:.2f} 分钟")
    if result["pdf"]:
        logger.info(f"PDF 报表: {result['pdf']}")
    if result["excel"]:
        logger.info(f"Excel 报表: {result['excel']}")
    if result["chart"]:
        logger.info(f"趋势图表: {result['chart']}")


def demo_history_query():
    logger.info("\n" + "=" * 70)
    logger.info("  演示: 历史记录组合查询与批量导出")
    logger.info("=" * 70)

    results = history.search_releases(risk_level="normal")
    logger.info(f"找到 {len(results)} 条常规发布记录")
    for r in results[:5]:
        logger.info(f"  #{r['id']} {r['version']} ({r['status']}) by {r['submitter']} @ {r['created_at']}")

    logger.info("\n批量导出 Excel...")
    path = history.export_releases(results, fmt="excel", filename_prefix="demo_export")
    if path:
        logger.info(f"导出文件: {path}")

    if results:
        logger.info(f"\n查看 {results[0]['version']} 的详细信息:")
        detail = history.get_release_detail(results[0]["id"])
        logger.info(f"  前置检查: {len(detail['prechecks'])} 项")
        logger.info(f"  审批记录: {len(detail['approvals'])} 条")
        logger.info(f"  灰度阶段: {len(detail['grayscale_stages'])} 个")
        logger.info(f"  回滚记录: {len(detail['rollbacks'])} 条")


def main():
    try:
        demo_full_flow()
        time.sleep(1)
        demo_drill()
        time.sleep(1)
        demo_weekly_report()
        time.sleep(1)
        demo_history_query()

        logger.info("\n" + "=" * 70)
        logger.info("  ✅ 所有演示完成！")
        logger.info("  数据库: data/release_system.db")
        logger.info("  操作日志: logs/YYYYMMDD.log")
        logger.info("  报表文件: reports/")
        logger.info("  请使用 'python main.py --help' 查看命令行使用方式")
        logger.info("=" * 70)
    except KeyboardInterrupt:
        logger.info("\n用户中断")


if __name__ == "__main__":
    main()
