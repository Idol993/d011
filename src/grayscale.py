from typing import List, Dict, Optional
from datetime import datetime, timedelta
from .config import GRAYSCALE_STAGES, DISTRIBUTION_CENTERS
from .logger import get_logger
from . import database as db
from . import notifier

logger = get_logger("grayscale")


def _pick_centers(count: int,
                  exclude: Optional[List[str]] = None,
                  allowed: Optional[List[str]] = None) -> List[str]:
    """
    选择灰度阶段的分拨中心。
    - allowed: 允许选择的中心（发布的 target_center_ids），None 表示全 12 个中心
    - exclude: 已部署过需排除的中心
    - count: 需要数量，-1 表示剩余全部
    数量不足时返回剩余所有中心（阶段自动缩减）
    """
    exclude = exclude or []
    if allowed:
        available = [cid for cid in allowed if cid not in exclude]
    else:
        available = [c["id"] for c in DISTRIBUTION_CENTERS if c["id"] not in exclude]
    if count == -1 or count >= len(available):
        return available
    return available[:count]


def _resolve_targets(release: Optional[Dict]) -> Optional[List[str]]:
    """从 release 记录解析 target_center_ids"""
    if not release:
        return None
    t = release.get("target_center_ids")
    if not t:
        return None
    if isinstance(t, str):
        import json as _json
        try:
            return _json.loads(t)
        except Exception:
            return None
    return list(t) if isinstance(t, list) else None


def get_deployed_centers(release_id: int) -> List[str]:
    deployed = set()
    for stage in db.list_grayscale_stages(release_id):
        deployed.update(stage["center_ids"])
    return list(deployed)


def start_next_stage(release_id: int) -> Optional[Dict]:
    release = db.get_release(release_id)
    if not release:
        logger.error(f"版本 {release_id} 不存在")
        return None

    status = release.get("status")
    if status == "paused":
        logger.warning(f"版本 {release['version']} 处于暂停状态，跳过灰度推进")
        return None

    if status != "approved" and status != "grayscale":
        logger.warning(f"版本 {release['version']} 状态={status}, 不能进入灰度")
        return None

    target_centers = _resolve_targets(release)

    stages_done = db.list_grayscale_stages(release_id)
    done_names = {s["stage_name"] for s in stages_done}
    next_stage_def = None
    for s in GRAYSCALE_STAGES:
        if s["name"] not in done_names:
            next_stage_def = s
            break

    if not next_stage_def:
        db.update_release_status(release_id, "released")
        logger.info(f"版本 {release['version']} 已完成所有灰度阶段，正式发布")
        return None

    deployed = get_deployed_centers(release_id)

    if target_centers:
        remaining_targets = [c for c in target_centers if c not in deployed]
        if not remaining_targets:
            db.update_release_status(release_id, "released")
            logger.info(
                f"版本 {release['version']} 已覆盖全部目标中心 {target_centers}，"
                f"跳过剩余灰度阶段，直接标记 released"
            )
            return None

    center_ids = _pick_centers(
        next_stage_def["centers"],
        exclude=deployed,
        allowed=target_centers,
    )
    if not center_ids:
        db.update_release_status(release_id, "released")
        logger.info(f"版本 {release['version']} 无剩余中心可部署，正式发布")
        return None

    stage_id = db.insert_grayscale_stage(release_id, next_stage_def["name"], center_ids)
    db.update_release_status(release_id, "grayscale")

    notifier.notify_grayscale_stage(release["version"], next_stage_def["name"], center_ids)
    scope_note = (
        f" (目标中心限定={target_centers})"
        if target_centers and len(target_centers) < len(DISTRIBUTION_CENTERS)
        else ""
    )
    logger.info(
        f"版本 {release['version']} 启动灰度阶段 {next_stage_def['name']}, "
        f"本次覆盖 {center_ids}{scope_note}"
    )

    return {
        "stage_id": stage_id,
        "stage_name": next_stage_def["name"],
        "center_ids": center_ids,
        "wait_hours": next_stage_def["wait_hours"],
        "target_centers": target_centers,
    }


def complete_current_stage(release_id: int):
    stage = db.get_current_stage(release_id)
    if stage:
        db.complete_grayscale_stage(stage["id"])
        logger.info(f"版本 {release_id} 灰度阶段 {stage['stage_name']} 标记完成")


def get_stage_plan() -> List[Dict]:
    return list(GRAYSCALE_STAGES)


def get_grayscale_status(release_id: int) -> Dict:
    release = db.get_release(release_id)
    if not release:
        return {}
    stages = db.list_grayscale_stages(release_id)
    current = db.get_current_stage(release_id)
    return {
        "release": release,
        "stages": stages,
        "current": current,
        "deployed_centers": get_deployed_centers(release_id),
    }
