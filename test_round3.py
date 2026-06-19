import sys
import os
import io
from datetime import datetime

if hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src import database as db
from src import grayscale
from src import monitor
from src import approval
from src import precheck
from src import report
from src import governance
from src.config import RELEASE_STATUS_LABELS

db.init_db()
PASS = 0
FAIL = 0

def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")

print("=" * 70)
print("需求1: 定向灰度全链路 (DC009/DC010 目标中心自动推进并结束")
print("=" * 70)

# 先造基线
baseline_id = db.insert_release(
    "v7.0.0-stable", risk_level="emergency",
    description="基线版本", submitter="管理员",
)
db.update_release_status(baseline_id, "released")
print(f"基线 v7.0.0-stable (ID={baseline_id}) 标记 released.")

passed, _, gov = governance.validate_release(
    "v7.1.0-small",
    target_center_ids=["DC009", "DC010"],
    risk_level="emergency",
)
rid = db.insert_release(
    "v7.1.0-small",
    risk_level="emergency",
    description="定向DC009 DC010小版本",
    submitter="测试开发",
    target_center_ids=["DC009", "DC010"],
    governance_bypassed=gov.get("bypassed_windows"),
    governance_used_stable=gov.get("used_stable"),
    emergency_urgent=True,
)
db.update_release_status(rid, "approved")
check("创建发布 v7.1.0 目标 DC009/DC010 approved", rid > 1)

# 阶段 1 pilot (1中心)
st1 = grayscale.start_next_stage(rid)
check(f"pilot 阶段: {st1['stage_name']}, 中心={st1['center_ids']}, target={st1['target_centers']}",
      st1 and st1["stage_name"] == "pilot"
      and set(st1["center_ids"]) == {"DC009"}
      and st1["target_centers"] == ["DC009", "DC010"])

# 阶段 2 small_batch 需要3个但只有DC010剩1个 -> 自动缩减
grayscale.complete_current_stage(rid)
st2 = grayscale.start_next_stage(rid)
check(f"small_batch 自动缩减到剩余目标中心 (剩余只有DC010)",
      st2 and st2["stage_name"] == "small_batch"
      and set(st2["center_ids"]) == {"DC010"})

# 阶段 3 medium_batch 无剩余目标 -> 自动标记 released 提前结束
grayscale.complete_current_stage(rid)
st3 = grayscale.start_next_stage(rid)
release_after = db.get_release(rid)
check(f"medium_batch后自动结束，状态已发布 released",
      st3 is None and release_after["status"] == "released",
      f"实际: stage={st3['stage_name'] if st3 else None} status={release_after['status']}")

print()
print("=" * 70)
print("需求3: release pause/resume 测试")
print("=" * 70)

r2 = db.insert_release(
    "v7.2.0-pausetest",
    risk_level="emergency",
    description="暂停恢复测试版本",
    submitter="测试",
    target_center_ids=["DC003", "DC004"],
    emergency_urgent=False,
    governance_bypassed=[],
    governance_used_stable="v7.0.0-stable",
)
db.update_release_status(r2, "approved")
s1 = grayscale.start_next_stage(r2)
check(f"创建并启动灰度 v7.2.0 灰度中 status={db.get_release(r2)['status']}",
      s1 and db.get_release(r2)["status"] == "grayscale")

ok = db.pause_release(r2, operator="运维老王", reason="分拨中心现场告警")
s_after_pause = db.get_release(r2)
check(f"暂停成功 status={s_after_pause['status']}",
      ok and s_after_pause["status"] == "paused"
      and s_after_pause["paused_by"] == "运维老王"
      and s_after_pause["paused_reason"] == "分拨中心现场告警")

# 暂停后再推进灰度，应该跳过
s_paused = grayscale.start_next_stage(r2)
check(f"暂停状态下 start_next_stage 返回 None", s_paused is None)

# 恢复
ns = db.resume_release(r2, operator="运维老王", reason="告警解除")
s_resume = db.get_release(r2)
check(f"恢复成功 新状态={ns} status={s_resume['status']}",
      ns == "grayscale" and s_resume["status"] == "grayscale"
      and s_resume["resumed_at"] is not None)

print()
print("=" * 70)
print("需求2: 中心维护窗口管理 + 治理拦截")
print("=" * 70)

mid = db.insert_center_maintenance(
    center_id="DC011",
    name="重庆机房搬迁",
    start="2026-06-15T00:00:00",
    end="2026-06-25T23:59:59",
    reason="重庆分拨中心机房搬迁，系统离线",
    operator="运维老李",
)
check(f"创建 DC011 维护窗口 id={mid}", mid >= 1)

mws = db.list_center_maintenances(only_active=True)
check(f"活动窗口数 >=1 (实际={len(mws)})", len(mws) >= 1)

# 发布包含DC011会被拦截
passed, viol, extra = governance.validate_release(
    "v99-test-dc011",
    target_center_ids=["DC011", "DC012"],
    risk_level="normal",
    now=datetime(2026, 6, 19, 10, 0, 0),
)
dc011_blocked = any("DC011" in v and "重庆机房搬迁" in v for v in viol)
msg_prefix = "拦截" if not passed else "放行"
check(f"普通发布命中DC011被拦截 {msg_prefix}",
      not passed and dc011_blocked, f"违规: {viol[:3]}")

# 紧急发布绕过
passed2, viol2, extra2 = governance.validate_release(
    "v99-emergency-dc011",
    target_center_ids=["DC011", "DC012"],
    risk_level="emergency",
    now=datetime(2026, 6, 19, 10, 0, 0),
)
bypassed_names = [b.get("name") for b in extra2.get("bypassed_windows") or []]
check(f"紧急发布绕过了重庆机房维护窗口(bypassed列表={bypassed_names})",
      any("重庆机房搬迁" in n for n in bypassed_names),
      f"通过={passed2}, bypassed={bypassed_names}")

# 删除维护窗口 后 再生成 report
db.delete_center_maintenance(mid, operator="测试")
mws2 = db.list_center_maintenances(center_id="DC011", only_active=True)
check(f"删除DC011维护窗口后，查询数量={len(mws2)}", len(mws2) == 0)

print()
print("=" * 70)
print("需求4: 中心维度看板 JSON 生成")
print("=" * 70)

# 生成看板前先插入一条DB维护窗口，确保 maintenance_now 有数据
db.insert_center_maintenance(
    center_id="DC009",
    name="DC009 618保障维护",
    start="2026-06-15T00:00:00",
    end="2026-06-25T23:59:59",
    reason="618大促期间维护",
    operator="测试程序",
    level="high",
)

result = report.generate_weekly_report()
check(f"中心看板路径生成: {result.get('center_dashboard')}",
      bool(result.get("center_dashboard")) and os.path.exists(result["center_dashboard"]))

import json
with open(result["center_dashboard"], encoding="utf-8") as f:
    cd = json.load(f)

summary = cd["summary"]
print(f"  总览: 中心数={summary['center_count']}, 发布总数={summary['release_total']}, "
      f"灰度中版本={summary['active_grayscale_releases']}, 维护中中心={summary['centers_in_maintenance']}")
check(f"看板summary.center_count == 12", summary["center_count"] == 12)

dc009 = next((c for c in cd["centers"] if c["center_id"] == "DC009"), None)
if dc009:
    check(f"DC009发布总数 > 0", dc009["release_total"] > 0)
    check(f"DC009 成功率字段存在", "success_rate" in dc009)
    check(f"DC009 latest_release.version == v7.1.0-small",
          dc009["latest_release"] and "v7.1.0" in dc009["latest_release"]["version"])

# grayscale_now = cd.get("grayscale_now") or []
grayscale_now = cd.get("grayscale_now") or []
print(f"  灰度中列表: {[g['center_id'] for g in grayscale_now]}")
maintenance_now = cd.get("maintenance_now") or []
print(f"  维护中列表: {[m['center_id'] for m in maintenance_now]}")
check(f"维护中中心 > 0 (至少有DC009的618)", len(maintenance_now) > 0)

print()
print("=" * 70)
print(f"  总计: PASS={PASS}, FAIL={FAIL}")
print("=" * 70)
sys.exit(0 if FAIL == 0 else 1)
