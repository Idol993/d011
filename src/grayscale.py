from typing import List, Dict, Optional
from datetime import datetime, timedelta
from .config import GRAYSCALE_STAGES, DISTRIBUTION_CENTERS
from .logger import get_logger
from . import database as db
from . import notifier

logger = get_logger("grayscale")


def _pick_centers(count: int, exclude: Optional[List[str]] = None) -> List[str]:
    exclude = exclude or []
    available = [c["id"] for c in DISTRIBUTION_CENTERS if c["id"] not in exclude]
    if count == -1 or count >= len(available):
        return available
    return available[:count]


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

    if release["status"] != "approved" and release["status"] != "grayscale":
        logger.warning(f"版本 {release['version']} 状态={release['status']}, 不能进入灰度")
        return None

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
    center_ids = _pick_centers(next_stage_def["centers"], exclude=deployed)
    if not center_ids:
        db.update_release_status(release_id, "released")
        return None

    stage_id = db.insert_grayscale_stage(release_id, next_stage_def["name"], center_ids)
    db.update_release_status(release_id, "grayscale")

    notifier.notify_grayscale_stage(release["version"], next_stage_def["name"], center_ids)
    logger.info(f"版本 {release['version']} 启动灰度阶段 {next_stage_def['name']}, "
                f"覆盖 {center_ids}")

    return {
        "stage_id": stage_id,
        "stage_name": next_stage_def["name"],
        "center_ids": center_ids,
        "wait_hours": next_stage_def["wait_hours"],
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
