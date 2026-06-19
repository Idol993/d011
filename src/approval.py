from typing import Dict, List, Optional, Tuple
from .config import APPROVAL_FLOWS, STAKEHOLDERS
from .logger import get_logger
from . import database as db
from . import notifier

logger = get_logger("approval")


def init_approval_flow(release_id: int) -> List[str]:
    release = db.get_release(release_id)
    if not release:
        raise ValueError(f"Release {release_id} not found")

    risk = release["risk_level"]
    flow_key = "emergency" if risk == "emergency" else "normal"
    roles = APPROVAL_FLOWS[flow_key]

    db.insert_approvals(release_id, roles)
    db.update_release_status(release_id, "awaiting_approval")

    logger.info(f"版本 {release['version']} 审批流程已初始化: {roles}")

    first_pending = db.get_pending_approval(release_id)
    if first_pending:
        notifier.notify_approval_pending(release["version"], first_pending["role"])

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
        notifier.notify_approval_pending(release["version"], next_pending["role"])

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

    return {
        "release": release,
        "approvals": [
            {
                "role": a["role"],
                "role_name": STAKEHOLDERS.get(a["role"], {}).get("name", a["role"]),
                "status": a["status"],
                "approver": a["approver"],
                "comment": a["comment"],
                "approved_at": a["approved_at"],
            }
            for a in approvals
        ],
        "pending_role": pending["role"] if pending else None,
        "all_approved": all_approved,
    }
