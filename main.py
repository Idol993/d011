import argparse
import sys
import os
import time
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.logger import get_logger
from src import database as db
from src import precheck
from src import approval
from src import grayscale
from src import monitor
from src import drill
from src import report
from src import history
from src import notifier
from src.config import DISTRIBUTION_CENTERS

logger = get_logger("main")


def cmd_submit(args):
    db.init_db()
    risk = "emergency" if args.emergency else "normal"
    release_id = db.insert_release(
        version=args.version,
        risk_level=risk,
        description=args.description or "",
        submitter=args.submitter,
        stable_version=args.stable_version,
    )
    notifier.notify_release_submitted(args.version, risk, args.submitter)
    logger.info(f"发布申请已提交: release_id={release_id}, version={args.version}, risk={risk}")

    if args.auto:
        logger.info("启动自动化流程: 前置检查 -> 审批 -> 灰度 -> 监控")
        passed, _ = precheck.run_prechecks(release_id)
        if not passed:
            logger.error("前置检查未通过，流程终止")
            return

        approval.init_approval_flow(release_id)
        roles = ["tech_lead", "ops_lead", "hub_manager"] if risk == "normal" else ["tech_lead", "hub_manager"]
        for role in roles:
            approver = {"tech_lead": "auto_tech", "ops_lead": "auto_ops", "hub_manager": "auto_mgr"}[role]
            ok, msg = approval.do_approve(release_id, role, approver, "自动审批通过")
            if not ok:
                logger.error(f"自动审批失败: {msg}")
                return

        monitor.start_monitoring()
        while True:
            stage = grayscale.start_next_stage(release_id)
            if not stage:
                break
            logger.info(f"等待灰度阶段 {stage['stage_name']} 观察期 {stage['wait_hours']} 小时(模拟1秒)...")
            time.sleep(1.0)
            sample = monitor.sample_release_once(release_id, inject_anomaly_center=args.inject_anomaly)
            if sample.get("anomalies"):
                monitor.trigger_rollback(release_id, sample["anomalies"])
                logger.warning("已触发回滚，发布流程终止")
                return
            grayscale.complete_current_stage(release_id)

        logger.info(f"版本 {args.version} 全量发布完成！")


def cmd_precheck(args):
    db.init_db()
    passed, results = precheck.run_prechecks(args.release_id)
    for r in results:
        mark = "✓" if r["passed"] else "✗"
        print(f"[{mark}] {r['detail']}")
    print(f"\n结论: {'全部通过' if passed else '存在未通过项'}")


def cmd_approval(args):
    db.init_db()
    if args.action == "init":
        roles = approval.init_approval_flow(args.release_id)
        print(f"已初始化审批流程: {roles}")
    elif args.action == "approve":
        ok, msg = approval.do_approve(args.release_id, args.role, args.approver, args.comment or "")
        print(f"{'成功' if ok else '失败'}: {msg}")
    elif args.action == "reject":
        ok, msg = approval.do_reject(args.release_id, args.role, args.approver, args.comment or "")
        print(f"{'成功' if ok else '失败'}: {msg}")
    elif args.action == "status":
        status = approval.get_approval_status(args.release_id)
        print(f"\n=== 版本 {status['release']['version']} 审批状态 ===")
        for a in status["approvals"]:
            mark = {"approved": "✓", "rejected": "✗", "pending": " "}[a["status"]]
            extra = f" - {a['approver']} @ {a['approved_at']}" if a['approver'] else ""
            print(f"  [{mark}] {a['role_name']}({a['role']}): {a['status']}{extra}")
        print(f"当前待审批: {status['pending_role'] or '无'}")
        print(f"全部通过: {status['all_approved']}")


def cmd_grayscale(args):
    db.init_db()
    if args.action == "start":
        stage = grayscale.start_next_stage(args.release_id)
        if stage:
            print(f"已启动灰度阶段: {stage['stage_name']}, 覆盖中心: {stage['center_ids']}")
        else:
            print("无可启动的灰度阶段")
    elif args.action == "complete":
        grayscale.complete_current_stage(args.release_id)
        print("已完成当前灰度阶段")
    elif args.action == "status":
        status = grayscale.get_grayscale_status(args.release_id)
        print(f"\n=== 版本 {status['release']['version']} 灰度状态 ===")
        for s in status["stages"]:
            print(f"  [{s['status']:9}] {s['stage_name']:12} -> {s['center_ids']}")
        print(f"已部署中心: {status['deployed_centers']}")
    elif args.action == "plan":
        for s in grayscale.get_stage_plan():
            print(f"  {s['name']:12} 覆盖{s['centers'] if s['centers'] != -1 else '全部'}个中心, 等待{s['wait_hours']}h")


def cmd_monitor(args):
    db.init_db()
    if args.action == "start":
        monitor.start_monitoring()
        print("监控线程已启动，Ctrl+C 停止")
        try:
            while True:
                time.sleep(10)
        except KeyboardInterrupt:
            monitor.stop_monitoring()
            print("已停止")
    elif args.action == "sample":
        result = monitor.sample_release_once(args.release_id, inject_anomaly_center=args.inject_anomaly)
        if not result:
            print("无监控数据")
            return
        print(f"\n=== 采样结果 ===")
        for rec in result["records"]:
            mark = "⚠" if rec["is_anomaly"] else " "
            print(f"  [{mark}] {rec['center_id']}: "
                  f"fail={rec['scan_fail_rate']*100:.2f}% "
                  f"delay={rec['sort_delay']:.1f}s "
                  f"loss={rec['loss_rate']*100:.3f}%"
                  + (f" -> {rec['reason']}" if rec["is_anomaly"] else ""))
        if result["anomalies"]:
            print(f"\n检测到 {len(result['anomalies'])} 个异常")
            if args.auto_rollback:
                monitor.trigger_rollback(args.release_id, result["anomalies"])
    elif args.action == "rollback":
        release = db.get_release(args.release_id)
        sample = monitor.sample_release_once(args.release_id, inject_anomaly_center=args.inject_anomaly)
        anomalies = sample.get("anomalies", [])
        if not anomalies:
            print("无异常，但强制回滚，生成模拟异常数据...")
            anomalies = [{
                "center_id": DISTRIBUTION_CENTERS[0]["id"],
                "scan_fail_rate": 0.05,
                "sort_delay": 200,
                "loss_rate": 0.01,
                "reason": "手动强制回滚",
            }]
        result = monitor.trigger_rollback(args.release_id, anomalies)
        print(f"\n{result['report']}")


def cmd_drill(args):
    db.init_db()
    if args.action == "create":
        result = drill.create_drill(args.name, args.target_version)
        print(f"已创建演练 #{result['drill_id']}: {args.name}")
        plan = result["plan"]
        print(f"涉及分拨中心: {plan['drill_centers']}")
        print(f"预计时长: {plan['expected_duration_minutes']} 分钟")
        print("步骤:")
        for s in plan["steps"]:
            print(f"  {s['step']}. {s['action']}")
        print("检查项:")
        for c in plan["checklist"]:
            print(f"  - {c}")
    elif args.action == "run":
        result = drill.run_drill(args.drill_id, simulated=not args.real)
        print(f"\n{result['result']}")
    elif args.action == "list":
        drills = drill.list_drills()
        print(f"{'ID':<5} {'名称':<20} {'版本':<12} {'状态':<10} {'创建时间':<20}")
        print("-" * 70)
        for d in drills:
            print(f"{d['id']:<5} {d['drill_name']:<20} {d.get('target_version') or '-':<12} "
                  f"{d['status']:<10} {d['created_at']:<20}")


def cmd_report(args):
    db.init_db()
    if args.action == "generate":
        result = report.generate_weekly_report()
        print(f"\n=== 周报 #{result['report_id']} ===")
        s = result["stats"]
        print(f"周期: {s['week_start']} ~ {s['week_end']}")
        print(f"发布总数: {s['release_total']}, 成功: {s['release_success']}, "
              f"成功率: {s['release_success_rate']*100:.2f}%")
        print(f"回滚次数: {s['rollback_count']}")
        print(f"平均审批时长: {s['avg_approval_seconds']/60:.2f} 分钟")
        if result["pdf"]:
            print(f"PDF: {result['pdf']}")
        if result["excel"]:
            print(f"Excel: {result['excel']}")
    elif args.action == "list":
        reports = db.list_weekly_reports()
        print(f"{'ID':<5} {'开始':<20} {'结束':<20} {'发布数':<8} {'成功数':<8} "
              f"{'回滚数':<8} {'平均审批(min)':<15}")
        print("-" * 90)
        for r in reports:
            print(f"{r['id']:<5} {r['week_start']:<20} {r['week_end']:<20} "
                  f"{r['release_total']:<8} {r['release_success']:<8} "
                  f"{r['rollback_count']:<8} {r['avg_approval_seconds']/60:<15.2f}")


def cmd_history(args):
    db.init_db()
    if args.action == "search":
        results = history.search_releases(
            version=args.version,
            status=args.status,
            risk_level=args.risk,
            start_time=args.start_time,
            end_time=args.end_time,
            center_id=args.center,
        )
        print(f"\n共找到 {len(results)} 条记录:\n")
        print(f"{'ID':<5} {'版本':<15} {'风险':<10} {'状态':<16} {'提交者':<10} "
              f"{'回滚':<6} {'创建时间':<20}")
        print("-" * 90)
        for r in results:
            print(f"{r['id']:<5} {r['version']:<15} {r['risk_level']:<10} "
                  f"{r['status']:<16} {r['submitter']:<10} "
                  f"{'是' if r['rollback_triggered'] else '否':<6} {r['created_at']:<20}")
    elif args.action == "detail":
        if args.version:
            detail = history.get_release_detail_by_version(args.version)
        else:
            detail = history.get_release_detail(args.release_id)
        if not detail:
            print("未找到记录")
            return
        r = detail["release"]
        print(f"\n=== 版本 {r['version']} 详情 ===")
        print(f"ID: {r['id']}, 风险: {r['risk_level']}, 状态: {r['status']}")
        print(f"提交者: {r['submitter']}, 稳定版本: {r.get('stable_version') or '-'}")
        print(f"描述: {r.get('description') or '-'}")
        print(f"触发回滚: {'是' if r['rollback_triggered'] else '否'}")
        if r.get("rollback_reason"):
            print(f"回滚原因: {r['rollback_reason']}")
        print(f"\n-- 前置检查 --")
        for p in detail["prechecks"]:
            mark = "✓" if p["passed"] else "✗"
            print(f"  [{mark}] {p['check_type']}: {p['result']} - {p['detail']}")
        print(f"\n-- 审批记录 --")
        for a in detail["approvals"]:
            print(f"  [{a['status']}] {a['role']}: {a.get('approver') or '-'} "
                  f"@ {a.get('approved_at') or '-'} | {a.get('comment') or ''}")
        print(f"\n-- 灰度阶段 --")
        for s in detail["grayscale_stages"]:
            print(f"  [{s['status']}] {s['stage_name']}: {s['center_ids']} "
                  f"({s['started_at']} ~ {s.get('completed_at') or '-'})")
        print(f"\n-- 回滚记录 --")
        for rb in detail["rollbacks"]:
            print(f"  #{rb['id']} [{rb['status']}] 影响{rb['affected_centers']} "
                  f"{rb['affected_parcels']}件, 原因: {rb['reason']}")
    elif args.action == "export":
        results = history.search_releases(
            version=args.version, status=args.status, risk_level=args.risk,
            start_time=args.start_time, end_time=args.end_time, center_id=args.center,
        )
        path = history.export_releases(results, fmt=args.format, filename_prefix=args.prefix)
        if path:
            print(f"已导出到: {path}")
        else:
            print("导出失败")


def cmd_logs(args):
    db.init_db()
    logs = db.list_operation_logs(limit=args.limit)
    print(f"{'时间':<20} {'模块':<10} {'动作':<12} {'操作者':<10} {'目标ID':<8} 详情")
    print("-" * 110)
    for l in logs:
        print(f"{l['created_at']:<20} {l['module']:<10} {l['action']:<12} "
              f"{l.get('operator') or '-':<10} {l.get('target_id') or '-':<8} {l.get('detail') or ''}")


def main():
    parser = argparse.ArgumentParser(
        prog="hub_release",
        description="快递快运分拨系统 - 版本发布与智能回滚自动化管理",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("submit", help="提交发布申请")
    p.add_argument("--version", required=True, help="发布版本号")
    p.add_argument("--description", help="版本描述")
    p.add_argument("--submitter", required=True, help="提交人")
    p.add_argument("--stable-version", help="上一稳定版本号")
    p.add_argument("--emergency", action="store_true", help="紧急发布")
    p.add_argument("--auto", action="store_true", help="自动执行完整流程(检查→审批→灰度→监控)")
    p.add_argument("--inject-anomaly", help="指定某分拨中心ID模拟异常(用于演示回滚)")
    p.set_defaults(func=cmd_submit)

    p = sub.add_parser("precheck", help="执行前置条件检查")
    p.add_argument("--release-id", type=int, required=True, help="发布ID")
    p.set_defaults(func=cmd_precheck)

    p = sub.add_parser("approval", help="审批流程管理")
    p.add_argument("action", choices=["init", "approve", "reject", "status"])
    p.add_argument("--release-id", type=int, required=True)
    p.add_argument("--role", choices=["tech_lead", "ops_lead", "hub_manager"])
    p.add_argument("--approver", help="审批人姓名")
    p.add_argument("--comment", help="审批意见")
    p.set_defaults(func=cmd_approval)

    p = sub.add_parser("grayscale", help="灰度发布管理")
    p.add_argument("action", choices=["start", "complete", "status", "plan"])
    p.add_argument("--release-id", type=int)
    p.set_defaults(func=cmd_grayscale)

    p = sub.add_parser("monitor", help="线上监控与回滚")
    p.add_argument("action", choices=["start", "sample", "rollback"])
    p.add_argument("--release-id", type=int)
    p.add_argument("--inject-anomaly", help="指定分拨中心ID模拟异常")
    p.add_argument("--auto-rollback", action="store_true", help="采样发现异常自动回滚")
    p.set_defaults(func=cmd_monitor)

    p = sub.add_parser("drill", help="回滚演练管理")
    p.add_argument("action", choices=["create", "run", "list"])
    p.add_argument("--name", help="演练名称")
    p.add_argument("--target-version", help="目标版本")
    p.add_argument("--drill-id", type=int, help="演练ID")
    p.add_argument("--real", action="store_true", help="真实演练(非模拟)")
    p.set_defaults(func=cmd_drill)

    p = sub.add_parser("report", help="周报统计报表")
    p.add_argument("action", choices=["generate", "list"])
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("history", help="历史记录查询与导出")
    p.add_argument("action", choices=["search", "detail", "export"])
    p.add_argument("--release-id", type=int)
    p.add_argument("--version")
    p.add_argument("--status")
    p.add_argument("--risk")
    p.add_argument("--start-time", help="YYYY-MM-DDTHH:MM:SS")
    p.add_argument("--end-time", help="YYYY-MM-DDTHH:MM:SS")
    p.add_argument("--center", help="分拨中心ID")
    p.add_argument("--format", choices=["excel", "csv", "json"], default="excel")
    p.add_argument("--prefix", default="release_export")
    p.set_defaults(func=cmd_history)

    p = sub.add_parser("logs", help="查看操作日志")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_logs)

    args = parser.parse_args()
    db.init_db()
    args.func(args)


if __name__ == "__main__":
    main()
