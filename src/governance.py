import re
from typing import List, Dict, Tuple, Optional
from datetime import datetime
from .logger import get_logger
from . import database as db
from .config import (
    FREEZE_WINDOWS,
    CENTER_MAINTENANCE_WINDOWS,
    DISTRIBUTION_CENTERS,
    GRAYSCALE_STAGES,
    SUCCESS_STATUSES,
    IN_PROGRESS_STATUSES,
)

logger = get_logger("governance")

VERSION_PATTERN = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)([-\w.]*)?$")


def _parse_version(version: str) -> Tuple[int, int, int]:
    m = VERSION_PATTERN.match(version)
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _projected_centers_for_release(
    existing_grayscale_stages: Optional[List[Dict]] = None,
    target_center_ids: Optional[List[str]] = None,
) -> List[str]:
    """
    估算某个发布最终会覆盖的分拨中心列表：
    - 若已有灰度阶段记录，则取已部署 + 未部署的剩余所有中心
    - 若有目标中心列表（用户指定），则用目标列表
    - 默认按 GRAYSCALE_STAGES 的 full 策略：所有中心
    """
    if target_center_ids:
        return list(target_center_ids)

    deployed: set = set()
    if existing_grayscale_stages:
        for s in existing_grayscale_stages:
            for cid in s.get("center_ids", []):
                deployed.add(cid)

    all_center_ids = [c["id"] for c in DISTRIBUTION_CENTERS]
    if not deployed:
        return all_center_ids

    has_full = any(stage["centers"] == -1 for stage in GRAYSCALE_STAGES)
    if has_full:
        return all_center_ids

    result = set(deployed)
    remaining = [c for c in all_center_ids if c not in deployed]
    for stage in GRAYSCALE_STAGES:
        if stage["centers"] == -1:
            return all_center_ids
        take = min(stage["centers"], len(remaining))
        for i in range(take):
            result.add(remaining.pop(0))
    return list(result)


def _in_freeze_window(
    now: datetime,
    target_center_ids: Optional[List[str]] = None,
) -> Tuple[bool, List[Dict]]:
    """
    检查当前时间和目标中心是否处于冻结窗口。
    返回 (是否拦截, 命中的窗口详情列表)
    """
    hits: List[Dict] = []
    target_set = set(target_center_ids) if target_center_ids else None

    for fw in FREEZE_WINDOWS:
        level = fw.get("level", "block_normal")
        fw_centers = fw.get("center_ids")

        if fw_centers and target_set:
            overlap = target_set & set(fw_centers)
            if not overlap:
                continue

        in_window = False
        window_detail = ""

        hour_range = fw.get("hour_range")
        if hour_range:
            h_start, h_end = hour_range
            if h_start <= now.hour < h_end:
                in_window = True
                window_detail = f"{h_start:02d}:00-{h_end:02d}:00"
        else:
            try:
                y = fw.get("year") or now.year
                s_raw = fw["start"]
                e_raw = fw["end"]
                def _to_dt(s):
                    if len(s) >= 10 and s[4] == "-":
                        return datetime.fromisoformat(s)
                    return datetime.fromisoformat(f"{y}-{s}")
                start_dt = _to_dt(s_raw)
                end_dt = _to_dt(e_raw)
                if start_dt <= now <= end_dt:
                    in_window = True
                    window_detail = f"{start_dt.date()} ~ {end_dt.date()}"
            except Exception as ex:
                logger.warning(f"解析冻结窗口失败: {fw}, err={ex}")

        if in_window:
            hits.append({
                **fw,
                "detail": window_detail,
                "affected_centers": (
                    list(set(fw_centers) & target_set)
                    if fw_centers and target_set else None
                ),
            })

    static_maintenance = dict(CENTER_MAINTENANCE_WINDOWS)
    dynamic_maintenance = db.list_all_center_maintenances_from_db()

    all_maintenance: Dict[str, List[Dict]] = {}
    for cid, lst in static_maintenance.items():
        all_maintenance.setdefault(cid, []).extend(lst)
    for cid, lst in dynamic_maintenance.items():
        all_maintenance.setdefault(cid, []).extend(lst)

    for cid, maintenance_list in all_maintenance.items():
        if target_set and cid not in target_set:
            continue
        for mw in maintenance_list:
            try:
                start_dt = datetime.fromisoformat(mw["start"])
                end_dt = datetime.fromisoformat(mw["end"])
                if start_dt <= now <= end_dt:
                    name = mw.get("_name") or f"中心维护-{cid}"
                    hits.append({
                        "name": f"{name}-{cid}",
                        "detail": f"{start_dt.isoformat(timespec='minutes')} ~ {end_dt.isoformat(timespec='minutes')}",
                        "center_ids": [cid],
                        "level": mw.get("level", "block_normal"),
                        "reason": mw.get("reason", f"{cid}离线维护"),
                    })
            except Exception as ex:
                logger.warning(f"解析中心维护窗口失败: {cid}:{mw}, err={ex}")

    return len(hits) > 0, hits


def check_release_windows(
    risk_level: str = "normal",
    target_center_ids: Optional[List[str]] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, List[str], List[Dict]]:
    """
    检查发布窗口。返回 (是否通过, 拦截原因列表, 命中窗口详情)
    - 普通发布：命中冻结窗口则拦截
    - 紧急发布：不拦截，但返回绕过的窗口用于历史留痕
    """
    now = now or datetime.now()
    blocked, hits = _in_freeze_window(now, target_center_ids)
    reasons = []
    bypassed = []

    for h in hits:
        level = h.get("level", "block_normal")
        if level == "block_normal" and risk_level != "emergency":
            center_part = ""
            if h.get("affected_centers"):
                center_part = f", 命中中心={h['affected_centers']}"
            elif h.get("center_ids"):
                center_part = f", 命中中心={h['center_ids']}"
            reasons.append(
                f"[规则4-发布窗口冻结] 窗口='{h['name']}' {h['detail']}{center_part}, "
                f"原因={h['reason']} — 普通发布在此期间禁止上线"
            )
        elif risk_level == "emergency":
            bypassed.append(h)

    passed = len(reasons) == 0
    return passed, reasons, bypassed


def validate_release(
    version: str,
    stable_version: str = None,
    risk_level: str = "normal",
    target_center_ids: Optional[List[str]] = None,
    now: Optional[datetime] = None,
) -> Tuple[bool, List[str], Dict]:
    """
    版本治理校验。
    返回: (是否通过, 违规原因列表, 附加信息字典)
    附加信息: {"bypassed_windows": [...], "latest_released": str, "used_stable": str}
    """
    violations: List[str] = []
    extra = {"bypassed_windows": [], "latest_released": None, "used_stable": None}

    existing = db.get_release_by_version(version)
    if existing:
        violations.append(
            f"[规则1-版本号唯一] 版本号 '{version}' 已存在 "
            f"(ID={existing['id']}, 状态={existing['status']}, "
            f"提交者={existing['submitter']}, 创建于={existing['created_at']})"
        )

    latest_released = db.get_latest_released_version()
    extra["latest_released"] = latest_released
    used_stable = stable_version if stable_version else latest_released
    extra["used_stable"] = used_stable

    new_ver = _parse_version(version)
    if used_stable:
        stable_ver = _parse_version(used_stable)
        if new_ver <= stable_ver:
            baseline_note = (
                f"(默认取最近成功发布版本='{used_stable}')"
                if not stable_version else f"(指定稳定版本='{stable_version}')"
            )
            violations.append(
                f"[规则2-版本号递增] 新版本 '{version}' 不高于基线版本 '{used_stable}' "
                f"{baseline_note}, 解析: {new_ver} <= {stable_ver}"
            )

    if stable_version and latest_released and stable_version != latest_released:
        sv = _parse_version(stable_version)
        lv = _parse_version(latest_released)
        if sv != lv:
            violations.append(
                f"[规则2-版本号递增] 指定稳定版本 '{stable_version}' 与最近成功发布 "
                f"'{latest_released}' 不一致 (解析: {sv} vs {lv})"
            )

    active_releases = db.get_active_releases_for_centers()
    if active_releases:
        new_projected = set(_projected_centers_for_release(target_center_ids=target_center_ids))
        conflict_items = []
        seen_ids = set()
        for r in active_releases:
            if r["id"] in seen_ids:
                continue
            seen_ids.add(r["id"])
            r_full = db.get_release(r["id"])
            r_targets = None
            if r_full and r_full.get("target_center_ids"):
                if isinstance(r_full["target_center_ids"], str):
                    import json as _json
                    try:
                        r_targets = _json.loads(r_full["target_center_ids"])
                    except Exception:
                        r_targets = None
                elif isinstance(r_full["target_center_ids"], list):
                    r_targets = r_full["target_center_ids"]
            stages = db.list_grayscale_stages(r["id"])
            active_projected = set(
                _projected_centers_for_release(
                    existing_grayscale_stages=stages,
                    target_center_ids=r_targets,
                )
            )
            overlap = list(new_projected & active_projected)
            if overlap:
                conflict_items.append(
                    f"'{r['version']}'(状态={r['status']}, 重叠中心={overlap})"
                )
            else:
                logger.info(
                    f"与活跃发布 '{r['version']}'({r['status']}) 无中心重叠，放行"
                )
        if conflict_items:
            violations.append(
                f"[规则3-无并发发布] 以下未完成发布与当前提交的目标中心存在重叠: "
                + "; ".join(conflict_items)
            )

    passed_window, window_violations, bypassed = check_release_windows(
        risk_level=risk_level, target_center_ids=target_center_ids, now=now,
    )
    violations.extend(window_violations)
    extra["bypassed_windows"] = bypassed

    passed = len(violations) == 0
    if passed:
        log_parts = [f"version={version}"]
        if latest_released:
            log_parts.append(f"baseline={used_stable}")
        if bypassed:
            log_parts.append(f"bypassed_windows={[b['name'] for b in bypassed]}")
        logger.info(f"版本治理校验通过: {' '.join(log_parts)}")
    else:
        logger.warning(f"版本治理校验未通过: version={version}, 违规={len(violations)}条")
        for v in violations:
            logger.warning(f"  {v}")

    return passed, violations, extra
