from typing import List, Dict
from .config import STAKEHOLDERS
from .logger import get_logger

logger = get_logger("notifier")


def notify_stakeholders(roles: List[str], subject: str, message: str,
                        include_all: bool = False):
    targets = {}
    if include_all:
        targets.update(STAKEHOLDERS)
    else:
        for role in roles:
            if role in STAKEHOLDERS:
                targets[role] = STAKEHOLDERS[role]

    for role, info in targets.items():
        logger.info(
            f"[通知 -> {info['name']}({role})] "
            f"邮件:{info['email']} 电话:{info['phone']} | "
            f"主题: {subject}"
        )
        logger.info(f"  内容: {message[:200]}{'...' if len(message) > 200 else ''}")

    return targets


def notify_release_submitted(version: str, risk_level: str, submitter: str):
    subject = f"【发布申请】版本 {version} ({risk_level}) 已提交"
    message = (
        f"版本 {version} 由 {submitter} 提交发布申请，风险级别: {risk_level}。\n"
        f"请相关负责人尽快完成前置条件检查与审批流程。"
    )
    notify_stakeholders([], subject, message, include_all=True)


def notify_precheck_result(version: str, passed: bool, details: str):
    status = "通过" if passed else "未通过"
    subject = f"【前置检查】版本 {version} 检查{status}"
    message = f"版本 {version} 前置条件检查{status}。\n详情:\n{details}"
    roles = ["tech_lead", "ops_lead"]
    notify_stakeholders(roles, subject, message)
    if not passed:
        notify_stakeholders(["hub_manager"], subject, message)


def notify_approval_pending(version: str, role: str):
    info = STAKEHOLDERS.get(role, {})
    subject = f"【待审批】版本 {version} 等待您的审批"
    message = f"版本 {version} 已进入审批流程，请 {info.get('name', role)} 及时处理。"
    notify_stakeholders([role], subject, message)


def notify_approval_result(version: str, passed: bool, approver: str):
    status = "通过" if passed else "已驳回"
    subject = f"【审批结果】版本 {version} {status}"
    message = f"版本 {version} 由 {approver} 审批{status}。"
    notify_stakeholders([], subject, message, include_all=True)


def notify_grayscale_stage(version: str, stage_name: str, centers: List[str]):
    subject = f"【灰度发布】版本 {version} - {stage_name} 阶段启动"
    message = f"版本 {version} 已进入 {stage_name} 阶段，覆盖分拨中心: {', '.join(centers)}。"
    notify_stakeholders(["tech_lead", "ops_lead", "hub_manager"], subject, message)


def notify_anomaly_detected(version: str, center_id: str, metrics: Dict, reason: str):
    subject = f"【异常告警】版本 {version} 在 {center_id} 检测到异常"
    message = (
        f"版本 {version} 在分拨中心 {center_id} 检测到指标异常:\n"
        f"{metrics}\n原因分析: {reason}\n系统将自动触发回滚。"
    )
    notify_stakeholders([], subject, message, include_all=True)


def notify_rollback_started(version: str, rollback_id: int, affected_centers: List[str],
                            affected_parcels: int, reason: str):
    subject = f"【紧急回滚】版本 {version} 自动回滚已启动"
    message = (
        f"回滚ID: {rollback_id}\n"
        f"版本: {version}\n"
        f"影响分拨中心: {', '.join(affected_centers)}\n"
        f"影响件量: {affected_parcels}\n"
        f"回滚原因: {reason}\n"
        f"系统正在执行自动回滚，请相关人员关注。"
    )
    notify_stakeholders([], subject, message, include_all=True)


def notify_rollback_completed(version: str, rollback_id: int, restored_version: str):
    subject = f"【回滚完成】版本 {version} 回滚完成，已恢复至 {restored_version}"
    message = (
        f"回滚ID: {rollback_id}\n"
        f"问题版本: {version}\n"
        f"已恢复版本: {restored_version}\n"
        f"监控流程已自动重启。"
    )
    notify_stakeholders([], subject, message, include_all=True)


def notify_drill_completed(drill_name: str, status: str, result: str):
    subject = f"【演练结果】回滚演练 {drill_name} {status}"
    message = f"演练 {drill_name} 执行结果: {status}\n{result}"
    notify_stakeholders(["tech_lead", "quality_lead", "hub_manager"], subject, message)


def notify_weekly_report_ready(report_id: int, pdf_path: str, excel_path: str):
    subject = f"【周报】发布与回滚统计周报 #{report_id} 已生成"
    message = f"周报已生成，附件:\nPDF: {pdf_path}\nExcel: {excel_path}"
    notify_stakeholders([], subject, message, include_all=True)
