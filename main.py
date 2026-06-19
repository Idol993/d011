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
from src import governance
from src import scheduler
from src.config import DISTRIBUTION_CENTERS, RELEASE_STATUS_LABELS, SCHEDULER_PID_PATH

logger = get_logger("main")


def cmd_submit(args):
    db.init_db()
    risk = "emergency" if args.emergency else "normal"
    urgent = args.urgent

    passed, violations = governance.validate_release(
        version=args.version,
        stable_version=args.stable_version,
        risk_level=risk,
    )
    if not passed:
        logger.error(f"版本治理校验未通过，发布被拦截:")
        for v in violations:
            print(f"  ✗ {v}")
        notifier.notify_governance_blocked(args.version, violations)
        return

    release_id = db.insert_release(
        version=args.version,
        risk_level=risk,
        description=args.description or "",
        submitter=args.submitter,
        stable_version=args.stable_version,
        emergency_urgent=urgent,
    )
    notifier.notify_release_submitted(args.version, risk, args.submitter)
    logger.info(f"发布申请已提交: release_id={release_id}, version={args.version}, risk={risk}, urgent={urgent}")

    if args.auto:
        logger.info("启动自动化流程: 前置检查 -> 审批 -> 灰度 -> 监控")
        passed, _ = precheck.run_prechecks(release_id)
        if not passed:
            logger.error("前置检查未通过，流程终止")
            return

        approval.init_approval_flow(release_id, emergency_urgent=urgent)
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
            logger.info(f"等待灰度阶段 {stage['stage_name']} 观察期(模拟1秒)...")
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
        roles = approval.init_approval_flow(args.release_id, emergency_urgent=args.urgent)
        print(f"已初始化审批流程: {roles}")
    elif args.action == "approve":
        ok, msg = approval.do_approve(args.release_id, args.role, args.approver, args.comment or "")
        print(f"{'成功' if ok else '失败'}: {msg}")
    elif args.action == "reject":
        ok, msg = approval.do_reject(args.release_id, args.role, args.approver, args.comment or "")
        print(f"{'成功' if ok else '失败'}: {msg}")
    elif args.action == "status":
        status = approval.get_approval_status(args.release_id)
        r = status["release"]
        print(f"\n=== 版本 {r['version']} 审批状态 ===")
        print(f"风险级别: {r['risk_level']}, 加急: {'是' if status['emergency_urgent'] else '否'}")
        print(f"超时阈值: {status['timeout_threshold_min']}分钟")
        for a in status["approvals"]:
            mark = {"approved": "✓", "rejected": "✗", "pending": " "}[a["status"]]
            timeout_flag = " ⚠超时" if a["is_timeout"] else ""
            reminded_flag = " [已提醒]" if a["timeout_reminded"] else ""
            wait_info = f" ({a['timeout_str']})" if a["timeout_str"] else ""
            dur_info = f" 耗时:{a['duration_str']}" if a["duration_str"] != "-" else ""
            print(f"  [{mark}] {a['role_name']}({a['role']}): {a['status']}{dur_info}{timeout_flag}{reminded_flag}{wait_info}")
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
        s = result["stats"]
        pw = s["prev_week"]
        print(f"\n=== 周报 #{result['report_id']} ===")
        print(f"周期: {s['week_start']} ~ {s['week_end']}")
        print(f"发布总数: {s['release_total']} (上周{pw['total']})")
        print(f"成功发布: {s['release_success']} (上周{pw['success']})")
        print(f"成功率: {s['release_success_rate']*100:.2f}% (上周{pw['success_rate']*100:.2f}%)")
        print(f"回滚次数: {s['rollback_count']} (上周{pw['rollback_count']})")
        print(f"失败/驳回: {s['release_failed']}, 进行中: {s['release_in_progress']}")
        print(f"平均审批时长: {s['avg_approval_seconds']/60:.2f} 分钟")
        print(f"\n-- 各状态 --")
        for status, count in s["status_counts"].items():
            if count > 0:
                print(f"  {RELEASE_STATUS_LABELS.get(status, status)}: {count}")
        print(f"\n-- 风险级别 --")
        for risk, count in s["risk_counts"].items():
            print(f"  {risk}: {count}")
        if s["per_role_avg"]:
            print(f"\n-- 各角色审批耗时 --")
            from src.config import STAKEHOLDERS
            for role, avg_s in s["per_role_avg"].items():
                name = STAKEHOLDERS.get(role, {}).get("name", role)
                print(f"  {name}({role}): {avg_s/60:.1f} 分钟")
        if result["pdf"]:
            print(f"\nPDF: {result['pdf']}")
        if result["excel"]:
            print(f"Excel: {result['excel']}")
    elif args.action == "list":
        reps = db.list_weekly_reports()
        print(f"{'ID':<5} {'开始':<20} {'结束':<20} {'发布':<6} {'成功':<6} {'回滚':<6} {'审批(min)':<10}")
        print("-" * 85)
        for r in reps:
            print(f"{r['id']:<5} {r['week_start']:<20} {r['week_end']:<20} "
                  f"{r['release_total']:<6} {r['release_success']:<6} "
                  f"{r['rollback_count']:<6} {r['avg_approval_seconds']/60:<10.1f}")


def cmd_scheduler(args):
    db.init_db()
    if args.action == "start":
        if scheduler.is_scheduler_running():
            print("调度器已在运行中")
            status = scheduler.get_scheduler_status()
            print(f"  PID: {status.get('pid', 'N/A')}")
            print(f"  下次周报生成: {status.get('next_report_time', 'N/A')}")
            return
        result = scheduler.start_scheduler(block=args.foreground)
        if args.foreground:
            return
        if result is not None:
            status = scheduler.get_scheduler_status()
            print("调度器已启动(后台)")
            print(f"  PID: {status.get('pid', 'N/A')}")
            print(f"  PID文件: {SCHEDULER_PID_PATH}")
            print(f"  下次周报生成: {status.get('next_report_time', 'N/A')}")
            print(f"  审批超时阈值: {status.get('approval_timeouts', {})}")
        else:
            print("调度器启动失败")
    elif args.action == "stop":
        if not scheduler.is_scheduler_running():
            print("调度器未在运行")
            return
        scheduler.stop_scheduler()
        print("调度器已停止")
    elif args.action == "status":
        status = scheduler.get_scheduler_status()
        print(f"\n=== 调度器状态 ===")
        print(f"运行中: {'是' if status['running'] else '否'}")
        print(f"下次周报生成: {status.get('next_report_time', 'N/A')}")
        print(f"审批超时阈值: {status.get('approval_timeouts', {})}")
        jobs = status.get("jobs", [])
        if jobs:
            print(f"\n已注册任务 ({len(jobs)}):")
            for j in jobs:
                print(f"  {j['job']} | 下次执行: {j['next_run']}")
        else:
            print("\n无已注册任务 (调度器未启动)")


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
        print(f"{'ID':<5} {'版本':<15} {'风险':<10} {'状态':<14} {'加急':<5} "
              f"{'回滚':<5} {'提交者':<10} {'创建时间':<20}")
        print("-" * 90)
        for r in results:
            print(f"{r['id']:<5} {r['version']:<15} {r['risk_level']:<10} "
                  f"{RELEASE_STATUS_LABELS.get(r['status'], r['status']):<14} "
                  f"{'是' if r.get('emergency_urgent') else '否':<5} "
                  f"{'是' if r['rollback_triggered'] else '否':<5} "
                  f"{r['submitter']:<10} {r['created_at']:<20}")
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
        print(f"ID: {r['id']}, 风险: {r['risk_level']}, 状态: {RELEASE_STATUS_LABELS.get(r['status'], r['status'])}")
        print(f"加急: {'是' if r.get('emergency_urgent') else '否'}, 提交者: {r['submitter']}")
        print(f"稳定版本: {r.get('stable_version') or '-'}, 描述: {r.get('description') or '-'}")
        print(f"触发回滚: {'是' if r['rollback_triggered'] else '否'}")
        if r.get("rollback_reason"):
            print(f"回滚原因: {r['rollback_reason']}")
        print(f"\n-- 前置检查 --")
        for p in detail["prechecks"]:
            mark = "✓" if p["passed"] else "✗"
            print(f"  [{mark}] {p['check_type']}: {p['result']} - {p['detail']}")
        print(f"\n-- 审批记录 --")
        for a in detail["approvals"]:
            dur_str = a.get("duration_str", "-")
            reminded = " [已超时提醒]" if a.get("timeout_reminded") else ""
            print(f"  [{a['status']}] {a['role']}: {a.get('approver') or '-'} "
                  f"耗时:{dur_str}{reminded} | {a.get('comment') or ''}")
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
    print(f"{'时间':<20} {'模块':<10} {'动作':<14} {'操作者':<10} {'目标ID':<8} 详情")
    print("-" * 110)
    for l in logs:
        print(f"{l['created_at']:<20} {l['module']:<10} {l['action']:<14} "
              f"{l.get('operator') or '-':<10} {l.get('target_id') or '-':<8} {l.get('detail') or ''}")


def cmd_scheduler_worker(args):
    db.init_db()
    scheduler._run_foreground_loop()


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "_scheduler_worker":
        db.init_db()
        scheduler._run_foreground_loop()
        return

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
    p.add_argument("--urgent", action="store_true", help="加急标记(审批超时阈值缩短)")
    p.add_argument("--auto", action="store_true", help="自动执行完整流程")
    p.add_argument("--inject-anomaly", help="指定某分拨中心ID模拟异常")
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
    p.add_argument("--urgent", action="store_true", help="加急审批")
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

    p = sub.add_parser("scheduler", help="后台调度管理")
    p.add_argument("action", choices=["start", "stop", "status"])
    p.add_argument("--foreground", action="store_true", help="前台运行(阻塞)")
    p.set_defaults(func=cmd_scheduler)

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
