from typing import Dict, List, Optional, Tuple
from .config import APPROVAL_FLOWS, APPROVAL_TIMEOUT_MINUTES, STAKEHOLDERS
from .logger import get_logger
from . import database as db
from . import notifier

logger = get_logger("approval")


def init_approval_flow(release_id: int, emergency_urgent: bool = False) -> List[str]:
    release = db.get_release(release_id)
    if not release:
        raise ValueError(f"Release {release_id} not found")

    risk = release["risk_level"]
    flow_key = "emergency" if risk == "emergency" else "normal"
    roles = APPROVAL_FLOWS[flow_key]

    db.insert_approvals(release_id, roles, emergency_urgent=emergency_urgent)
    db.update_release_status(release_id, "awaiting_approval")

    logger.info(f"版本 {release['version']} 审批流程已初始化: {roles}, 加急={emergency_urgent}")

    first_pending = db.get_pending_approval(release_id)
    if first_pending:
        notifier.notify_approval_pending(release["version"], first_pending["role"],
                                          emergency_urgent=emergency_urgent)

    return roles


def do_approve(release_id: int, role: str, approver: str,
               comment: str = "") -> Tuple[bool, str]:
    release = db.get_release(release_id)
    if not release:
        return False, "发布记录不存在"

    pending = db.get_pending_approval(release_id)
    if not pending:
        return False, "当前无待审批项"
    if pending["role"] != role:
        return False, f"当前待审批角色为 {pending['role']}, 非 {role}"

    ok = db.approve(pending["id"], approver, comment)
    if not ok:
        return False, "审批操作失败"

    notifier.notify_approval_result(release["version"], True, approver)
    logger.info(f"{approver}({role}) 审批通过版本 {release['version']}")

    if db.check_all_approved(release_id):
        db.update_release_status(release_id, "approved")
        logger.info(f"版本 {release['version']} 全部审批通过")
        return True, "全部审批通过，可进入灰度发布"

    next_pending = db.get_pending_approval(release_id)
    if next_pending:
        urgent = bool(release.get("emergency_urgent", 0))
        notifier.notify_approval_pending(release["version"], next_pending["role"],
                                          emergency_urgent=urgent)

    return True, "审批通过，等待下一级审批"


def do_reject(release_id: int, role: str, approver: str,
              comment: str = "") -> Tuple[bool, str]:
    release = db.get_release(release_id)
    if not release:
        return False, "发布记录不存在"

    pending = db.get_pending_approval(release_id)
    if not pending:
        return False, "当前无待审批项"
    if pending["role"] != role:
        return False, f"当前待审批角色为 {pending['role']}, 非 {role}"

    ok = db.reject(pending["id"], approver, comment)
    if not ok:
        return False, "审批操作失败"

    db.update_release_status(release_id, "rejected")
    notifier.notify_approval_result(release["version"], False, approver)
    logger.warning(f"{approver}({role}) 驳回版本 {release['version']}, 原因: {comment}")
    return True, "已驳回发布申请"


def get_approval_status(release_id: int) -> Dict:
    release = db.get_release(release_id)
    if not release:
        return {}
    approvals = db.list_approvals(release_id)
    pending = db.get_pending_approval(release_id)
    all_approved = db.check_all_approved(release_id)
    role_durations = db.get_per_role_durations(release_id)
    risk = release["risk_level"]
    timeout_min = APPROVAL_TIMEOUT_MINUTES.get(risk, 480)
    is_urgent = bool(release.get("emergency_urgent", 0))

    approval_details = []
    for a in approvals:
        dur = a.get("duration_seconds")
        dur_str = f"{dur:.0f}s ({dur/60:.1f}min)" if dur else "-"
        is_timeout = False
        timeout_str = ""
        if a["status"] == "pending":
            if a.get("created_at"):
                try:
                    elapsed = (datetime.now() - datetime.fromisoformat(a["created_at"])).total_seconds()
                    if elapsed / 60 > timeout_min:
                        is_timeout = True
                    timeout_str = f"已等待{elapsed/60:.0f}min"
                except Exception:
                    pass
        reminded = bool(a.get("timeout_reminded", 0))

        approval_details.append({
            "role": a["role"],
            "role_name": STAKEHOLDERS.get(a["role"], {}).get("name", a["role"]),
            "status": a["status"],
            "approver": a.get("approver"),
            "comment": a.get("comment"),
            "approved_at": a.get("approved_at"),
            "duration_seconds": dur,
            "duration_str": dur_str,
            "is_timeout": is_timeout,
            "timeout_reminded": reminded,
            "timeout_str": timeout_str,
        })

    return {
        "release": release,
        "approvals": approval_details,
        "pending_role": pending["role"] if pending else None,
        "all_approved": all_approved,
        "role_durations": role_durations,
        "timeout_threshold_min": timeout_min,
        "emergency_urgent": is_urgent,
    }


from datetime import datetime
