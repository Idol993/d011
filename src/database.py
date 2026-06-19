import sqlite3
import json
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, List, Dict, Any
from .config import DB_PATH
from .logger import get_logger

logger = get_logger("database")


def _dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Database error: {e}")
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        cur = conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS releases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                version TEXT NOT NULL UNIQUE,
                risk_level TEXT NOT NULL,
                description TEXT,
                submitter TEXT NOT NULL,
                status TEXT NOT NULL,
                stable_version TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                rollback_triggered INTEGER DEFAULT 0,
                rollback_reason TEXT,
                emergency_urgent INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS prechecks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                check_type TEXT NOT NULL,
                result REAL NOT NULL,
                passed INTEGER NOT NULL,
                detail TEXT,
                checked_at TEXT NOT NULL,
                FOREIGN KEY (release_id) REFERENCES releases(id)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                approver TEXT,
                status TEXT NOT NULL,
                comment TEXT,
                approved_at TEXT,
                created_at TEXT NOT NULL,
                duration_seconds REAL,
                timeout_reminded INTEGER DEFAULT 0,
                FOREIGN KEY (release_id) REFERENCES releases(id)
            );

            CREATE TABLE IF NOT EXISTS grayscale_stages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                stage_name TEXT NOT NULL,
                center_ids TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                FOREIGN KEY (release_id) REFERENCES releases(id)
            );

            CREATE TABLE IF NOT EXISTS monitor_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                stage_id INTEGER,
                center_id TEXT NOT NULL,
                scan_fail_rate REAL NOT NULL,
                sort_delay REAL NOT NULL,
                loss_rate REAL NOT NULL,
                is_anomaly INTEGER NOT NULL,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (release_id) REFERENCES releases(id)
            );

            CREATE TABLE IF NOT EXISTS rollback_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                release_id INTEGER NOT NULL,
                stage_id INTEGER,
                affected_centers TEXT NOT NULL,
                affected_parcels INTEGER NOT NULL,
                reason TEXT NOT NULL,
                anomaly_detail TEXT,
                rolled_back_version TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS drill_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drill_name TEXT NOT NULL,
                target_version TEXT,
                plan TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS weekly_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                release_total INTEGER NOT NULL,
                release_success INTEGER NOT NULL,
                rollback_count INTEGER NOT NULL,
                avg_approval_seconds REAL NOT NULL,
                pdf_path TEXT,
                excel_path TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                module TEXT NOT NULL,
                action TEXT NOT NULL,
                operator TEXT,
                target_id INTEGER,
                detail TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scheduler_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                result TEXT,
                error_detail TEXT,
                duration_seconds REAL
            );
            """
        )
        _migrate(conn)
        logger.info("Database initialized")


def _migrate(conn):
    cur = conn.cursor()
    try:
        cur.execute("SELECT emergency_urgent FROM releases LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE releases ADD COLUMN emergency_urgent INTEGER DEFAULT 0")
        logger.info("Migrated: added releases.emergency_urgent")

    try:
        cur.execute("SELECT duration_seconds FROM approvals LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE approvals ADD COLUMN duration_seconds REAL")
        logger.info("Migrated: added approvals.duration_seconds")

    try:
        cur.execute("SELECT timeout_reminded FROM approvals LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE approvals ADD COLUMN timeout_reminded INTEGER DEFAULT 0")
        logger.info("Migrated: added approvals.timeout_reminded")

    try:
        cur.execute("SELECT governance_bypassed FROM releases LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE releases ADD COLUMN governance_bypassed TEXT")
        logger.info("Migrated: added releases.governance_bypassed")

    try:
        cur.execute("SELECT target_center_ids FROM releases LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE releases ADD COLUMN target_center_ids TEXT")
        logger.info("Migrated: added releases.target_center_ids")

    try:
        cur.execute("SELECT governance_used_stable FROM releases LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE releases ADD COLUMN governance_used_stable TEXT")
        logger.info("Migrated: added releases.governance_used_stable")

    try:
        cur.execute("SELECT json_path FROM weekly_reports LIMIT 1")
    except sqlite3.OperationalError:
        cur.execute("ALTER TABLE weekly_reports ADD COLUMN json_path TEXT")
        logger.info("Migrated: added weekly_reports.json_path")


def insert_release(version: str, risk_level: str, description: str,
                   submitter: str, stable_version: Optional[str],
                   emergency_urgent: bool = False,
                   governance_bypassed: Optional[List[Dict]] = None,
                   target_center_ids: Optional[List[str]] = None,
                   governance_used_stable: Optional[str] = None) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        bypassed_json = json.dumps(governance_bypassed, ensure_ascii=False) if governance_bypassed else None
        targets_json = json.dumps(target_center_ids, ensure_ascii=False) if target_center_ids else None
        cur.execute(
            """INSERT INTO releases
               (version, risk_level, description, submitter, status,
                stable_version, created_at, updated_at, emergency_urgent,
                governance_bypassed, target_center_ids, governance_used_stable)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)""",
            (version, risk_level, description, submitter, stable_version,
             now, now, int(emergency_urgent), bypassed_json, targets_json, governance_used_stable),
        )
        release_id = cur.lastrowid
        _log(conn, "release", "create", submitter, release_id,
             f"创建发布申请: version={version}, risk={risk_level}, urgent={emergency_urgent}"
             + (f", bypassed={len(governance_bypassed)} 个窗口" if governance_bypassed else ""))
        return release_id


def update_release_status(release_id: int, status: str, rollback_reason: Optional[str] = None):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        if rollback_reason:
            cur.execute(
                "UPDATE releases SET status=?, updated_at=?, rollback_triggered=1, rollback_reason=? WHERE id=?",
                (status, now, rollback_reason, release_id),
            )
        else:
            cur.execute(
                "UPDATE releases SET status=?, updated_at=? WHERE id=?",
                (status, now, release_id),
            )
        _log(conn, "release", "update_status", None, release_id, f"状态变更为 {status}")


def get_release(release_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM releases WHERE id=?", (release_id,))
        return cur.fetchone()


def get_release_by_version(version: str) -> Optional[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM releases WHERE version=?", (version,))
        return cur.fetchone()


def get_latest_released_version() -> Optional[str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT version FROM releases WHERE status='released' AND rollback_triggered=0 "
            "ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row["version"] if row else None


def get_active_releases_for_centers() -> List[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT r.id, r.version, r.status, r.risk_level, gs.center_ids "
            "FROM releases r "
            "LEFT JOIN grayscale_stages gs ON r.id = gs.release_id AND gs.status = 'running' "
            "WHERE r.status IN ('awaiting_approval', 'approved', 'grayscale')"
        )
        rows = cur.fetchall()
        for r in rows:
            r["center_ids"] = json.loads(r["center_ids"]) if r["center_ids"] else []
        return rows


def list_releases(filters: Optional[Dict] = None) -> List[Dict]:
    sql = "SELECT * FROM releases WHERE 1=1"
    params = []
    if filters:
        if filters.get("version"):
            sql += " AND version LIKE ?"
            params.append(f"%{filters['version']}%")
        if filters.get("status"):
            sql += " AND status = ?"
            params.append(filters["status"])
        if filters.get("start_time"):
            sql += " AND created_at >= ?"
            params.append(filters["start_time"])
        if filters.get("end_time"):
            sql += " AND created_at <= ?"
            params.append(filters["end_time"])
        if filters.get("risk_level"):
            sql += " AND risk_level = ?"
            params.append(filters["risk_level"])
    sql += " ORDER BY id DESC"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def insert_precheck(release_id: int, check_type: str, result: float,
                    passed: bool, detail: str):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO prechecks
               (release_id, check_type, result, passed, detail, checked_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (release_id, check_type, result, int(passed), detail, now),
        )
        _log(conn, "precheck", check_type, None, release_id,
             f"检查类型={check_type}, 结果={result}, 通过={passed}")


def list_prechecks(release_id: int) -> List[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM prechecks WHERE release_id=? ORDER BY id", (release_id,))
        return cur.fetchall()


def insert_approvals(release_id: int, roles: List[str], emergency_urgent: bool = False):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        for role in roles:
            cur.execute(
                """INSERT INTO approvals
                   (release_id, role, status, created_at, timeout_reminded)
                   VALUES (?, ?, 'pending', ?, 0)""",
                (release_id, role, now),
            )
        _log(conn, "approval", "init", None, release_id,
             f"初始化审批流程: roles={roles}, urgent={emergency_urgent}")


def approve(approval_id: int, approver: str, comment: str) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT created_at FROM approvals WHERE id=? AND status='pending'", (approval_id,))
        row = cur.fetchone()
        duration = None
        if row and row["created_at"]:
            try:
                duration = (datetime.fromisoformat(now) - datetime.fromisoformat(row["created_at"])).total_seconds()
            except Exception:
                pass
        cur.execute(
            "UPDATE approvals SET status='approved', approver=?, comment=?, approved_at=?, duration_seconds=? "
            "WHERE id=? AND status='pending'",
            (approver, comment, now, duration, approval_id),
        )
        if cur.rowcount > 0:
            cur.execute("SELECT release_id FROM approvals WHERE id=?", (approval_id,))
            r2 = cur.fetchone()
            _log(conn, "approval", "approve", approver, r2["release_id"],
                 f"审批ID={approval_id}, 通过, 耗时={duration:.0f}s" if duration else f"审批ID={approval_id}, 通过")
            return True
        return False


def reject(approval_id: int, approver: str, comment: str) -> bool:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT created_at FROM approvals WHERE id=? AND status='pending'", (approval_id,))
        row = cur.fetchone()
        duration = None
        if row and row["created_at"]:
            try:
                duration = (datetime.fromisoformat(now) - datetime.fromisoformat(row["created_at"])).total_seconds()
            except Exception:
                pass
        cur.execute(
            "UPDATE approvals SET status='rejected', approver=?, comment=?, approved_at=?, duration_seconds=? "
            "WHERE id=? AND status='pending'",
            (approver, comment, now, duration, approval_id),
        )
        if cur.rowcount > 0:
            cur.execute("SELECT release_id FROM approvals WHERE id=?", (approval_id,))
            r2 = cur.fetchone()
            _log(conn, "approval", "reject", approver, r2["release_id"],
                 f"审批ID={approval_id}, 拒绝")
            return True
        return False


def list_approvals(release_id: int) -> List[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM approvals WHERE release_id=? ORDER BY id", (release_id,))
        return cur.fetchall()


def check_all_approved(release_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM approvals WHERE release_id=?", (release_id,))
        rows = cur.fetchall()
        if not rows:
            return False
        return all(r["status"] == "approved" for r in rows)


def get_pending_approval(release_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM approvals WHERE release_id=? AND status='pending' ORDER BY id LIMIT 1",
            (release_id,),
        )
        return cur.fetchone()


def get_timed_out_approvals(timeout_minutes: int) -> List[Dict]:
    now_iso = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT a.*, r.version, r.risk_level
               FROM approvals a
               JOIN releases r ON a.release_id = r.id
               WHERE a.status = 'pending'
               AND a.timeout_reminded = 0
               AND julianday(?) - julianday(a.created_at) > ? / 1440.0
               ORDER BY a.id""",
            (now_iso, timeout_minutes),
        )
        return cur.fetchall()


def mark_timeout_reminded(approval_id: int):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE approvals SET timeout_reminded=1 WHERE id=?",
            (approval_id,),
        )
        _log(conn, "approval", "timeout_remind", None, None,
             f"审批ID={approval_id} 超时提醒已发送")


def insert_grayscale_stage(release_id: int, stage_name: str, center_ids: List[str]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO grayscale_stages
               (release_id, stage_name, center_ids, status, started_at)
               VALUES (?, ?, ?, 'running', ?)""",
            (release_id, stage_name, json.dumps(center_ids, ensure_ascii=False), now),
        )
        stage_id = cur.lastrowid
        _log(conn, "grayscale", "start", None, release_id,
             f"阶段={stage_name}, 分拨中心={center_ids}")
        return stage_id


def complete_grayscale_stage(stage_id: int):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE grayscale_stages SET status='completed', completed_at=? WHERE id=?",
            (now, stage_id),
        )
        cur.execute("SELECT release_id FROM grayscale_stages WHERE id=?", (stage_id,))
        row = cur.fetchone()
        if row:
            _log(conn, "grayscale", "complete", None, row["release_id"],
                 f"阶段ID={stage_id} 完成")


def list_grayscale_stages(release_id: int) -> List[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM grayscale_stages WHERE release_id=? ORDER BY id",
            (release_id,),
        )
        rows = cur.fetchall()
        for r in rows:
            r["center_ids"] = json.loads(r["center_ids"]) if r["center_ids"] else []
        return rows


def get_current_stage(release_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM grayscale_stages WHERE release_id=? AND status='running' ORDER BY id DESC LIMIT 1",
            (release_id,),
        )
        row = cur.fetchone()
        if row:
            row["center_ids"] = json.loads(row["center_ids"]) if row["center_ids"] else []
        return row


def insert_monitor_record(release_id: int, stage_id: Optional[int], center_id: str,
                          scan_fail_rate: float, sort_delay: float,
                          loss_rate: float, is_anomaly: bool) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO monitor_records
               (release_id, stage_id, center_id, scan_fail_rate, sort_delay,
                loss_rate, is_anomaly, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (release_id, stage_id, center_id, scan_fail_rate, sort_delay,
             loss_rate, int(is_anomaly), now),
        )
        return cur.lastrowid


def list_monitor_records(release_id: int, limit: int = 100) -> List[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM monitor_records WHERE release_id=? ORDER BY id DESC LIMIT ?",
            (release_id, limit),
        )
        return cur.fetchall()


def insert_rollback(release_id: int, stage_id: Optional[int], affected_centers: List[str],
                    affected_parcels: int, reason: str, anomaly_detail: str,
                    rolled_back_version: str) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO rollback_records
               (release_id, stage_id, affected_centers, affected_parcels,
                reason, anomaly_detail, rolled_back_version, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'rolling_back', ?)""",
            (release_id, stage_id, json.dumps(affected_centers, ensure_ascii=False),
             affected_parcels, reason, anomaly_detail, rolled_back_version, now),
        )
        rollback_id = cur.lastrowid
        _log(conn, "rollback", "trigger", None, release_id,
             f"回滚ID={rollback_id}, 原因={reason}, 影响分拨={affected_centers}")
        return rollback_id


def complete_rollback(rollback_id: int):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE rollback_records SET status='completed', completed_at=? WHERE id=?",
            (now, rollback_id),
        )
        cur.execute("SELECT release_id FROM rollback_records WHERE id=?", (rollback_id,))
        row = cur.fetchone()
        if row:
            _log(conn, "rollback", "complete", None, row["release_id"],
                 f"回滚ID={rollback_id} 完成")


def list_rollbacks(release_id: Optional[int] = None) -> List[Dict]:
    sql = "SELECT * FROM rollback_records"
    params = []
    if release_id:
        sql += " WHERE release_id=?"
        params.append(release_id)
    sql += " ORDER BY id DESC"
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        for r in rows:
            r["affected_centers"] = json.loads(r["affected_centers"]) if r["affected_centers"] else []
        return rows


def insert_drill(drill_name: str, target_version: Optional[str], plan: Dict) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO drill_records
               (drill_name, target_version, plan, status, created_at)
               VALUES (?, ?, ?, 'created', ?)""",
            (drill_name, target_version, json.dumps(plan, ensure_ascii=False), now),
        )
        drill_id = cur.lastrowid
        _log(conn, "drill", "create", None, drill_id,
             f"演练名称={drill_name}, 目标版本={target_version}")
        return drill_id


def update_drill_status(drill_id: int, status: str, result: Optional[str] = None):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        if status == "running":
            cur.execute(
                "UPDATE drill_records SET status=?, started_at=? WHERE id=?",
                (status, now, drill_id),
            )
        elif status == "completed":
            cur.execute(
                "UPDATE drill_records SET status=?, result=?, completed_at=? WHERE id=?",
                (status, result, now, drill_id),
            )
        else:
            cur.execute(
                "UPDATE drill_records SET status=? WHERE id=?",
                (status, drill_id),
            )
        _log(conn, "drill", "status_change", None, drill_id, f"状态={status}")


def list_drills() -> List[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM drill_records ORDER BY id DESC")
        rows = cur.fetchall()
        for r in rows:
            r["plan"] = json.loads(r["plan"]) if r["plan"] else {}
        return rows


def insert_weekly_report(week_start: str, week_end: str, release_total: int,
                         release_success: int, rollback_count: int,
                         avg_approval_seconds: float, pdf_path: Optional[str],
                         excel_path: Optional[str]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO weekly_reports
               (week_start, week_end, release_total, release_success,
                rollback_count, avg_approval_seconds, pdf_path, excel_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (week_start, week_end, release_total, release_success, rollback_count,
             avg_approval_seconds, pdf_path, excel_path, now),
        )
        report_id = cur.lastrowid
        _log(conn, "report", "create", None, report_id,
             f"周报 {week_start} ~ {week_end}")
        return report_id


def list_weekly_reports() -> List[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM weekly_reports ORDER BY id DESC")
        return cur.fetchall()


def list_operation_logs(limit: int = 200) -> List[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM operation_logs ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()


def _log(conn, module: str, action: str, operator: Optional[str],
         target_id: Optional[int], detail: str):
    now = datetime.now().isoformat(timespec="seconds")
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO operation_logs
           (module, action, operator, target_id, detail, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (module, action, operator, target_id, detail, now),
    )
    logger.info(f"[{module}] {action} | target={target_id} | {detail}")


def get_approval_duration_seconds(release_id: int) -> Optional[float]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT MIN(created_at) AS first_at, MAX(approved_at) AS last_at "
            "FROM approvals WHERE release_id=? AND status='approved'",
            (release_id,),
        )
        row = cur.fetchone()
        if row and row["first_at"] and row["last_at"]:
            try:
                start = datetime.fromisoformat(row["first_at"])
                end = datetime.fromisoformat(row["last_at"])
                return (end - start).total_seconds()
            except Exception:
                return None
        return None


def get_per_role_durations(release_id: int) -> Dict[str, Optional[float]]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT role, duration_seconds FROM approvals WHERE release_id=? AND status='approved'",
            (release_id,),
        )
        rows = cur.fetchall()
        return {r["role"]: r["duration_seconds"] for r in rows}


def get_release(release_id: int) -> Optional[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM releases WHERE id=?", (release_id,))
        row = cur.fetchone()
        if row:
            if row.get("governance_bypassed"):
                try:
                    row["governance_bypassed"] = json.loads(row["governance_bypassed"])
                except Exception:
                    pass
            if row.get("target_center_ids"):
                try:
                    row["target_center_ids"] = json.loads(row["target_center_ids"])
                except Exception:
                    pass
        return row


def get_release_by_version(version: str) -> Optional[Dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM releases WHERE version=?", (version,))
        row = cur.fetchone()
        if row:
            if row.get("governance_bypassed"):
                try:
                    row["governance_bypassed"] = json.loads(row["governance_bypassed"])
                except Exception:
                    pass
            if row.get("target_center_ids"):
                try:
                    row["target_center_ids"] = json.loads(row["target_center_ids"])
                except Exception:
                    pass
        return row


def insert_scheduler_run(job_name: str, started_at: Optional[str] = None) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO scheduler_runs
               (job_name, started_at, status) VALUES (?, ?, 'running')""",
            (job_name, started_at or now),
        )
        return cur.lastrowid


def complete_scheduler_run(run_id: int, status: str,
                           result: Optional[str] = None,
                           error_detail: Optional[str] = None,
                           finished_at: Optional[str] = None):
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT started_at FROM scheduler_runs WHERE id=?", (run_id,))
        row = cur.fetchone()
        duration = None
        if row and row["started_at"]:
            try:
                duration = (datetime.fromisoformat(finished_at or now)
                            - datetime.fromisoformat(row["started_at"])).total_seconds()
            except Exception:
                pass
        cur.execute(
            """UPDATE scheduler_runs
               SET status=?, result=?, error_detail=?,
                   finished_at=?, duration_seconds=?
               WHERE id=?""",
            (status, result, error_detail, finished_at or now, duration, run_id),
        )
        _log(conn, "scheduler", status, None, None,
             f"run_id={run_id}, job={status}, 耗时={duration:.1f}s" if duration else f"run_id={run_id}, job={status}")


def list_scheduler_runs(job_name: Optional[str] = None, limit: int = 20) -> List[Dict]:
    sql = "SELECT * FROM scheduler_runs"
    params = []
    if job_name:
        sql += " WHERE job_name=?"
        params.append(job_name)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(sql, params)
        return cur.fetchall()


def get_last_scheduler_run(job_name: str) -> Optional[Dict]:
    runs = list_scheduler_runs(job_name=job_name, limit=1)
    return runs[0] if runs else None


def insert_weekly_report(week_start: str, week_end: str, release_total: int,
                         release_success: int, rollback_count: int,
                         avg_approval_seconds: float, pdf_path: Optional[str],
                         excel_path: Optional[str],
                         json_path: Optional[str] = None) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO weekly_reports
               (week_start, week_end, release_total, release_success,
                rollback_count, avg_approval_seconds, pdf_path, excel_path, json_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (week_start, week_end, release_total, release_success, rollback_count,
             avg_approval_seconds, pdf_path, excel_path, json_path, now),
        )
        report_id = cur.lastrowid
        _log(conn, "report", "create", None, report_id,
             f"周报 {week_start} ~ {week_end}")
        return report_id
