import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

DB_PATH = os.path.join(DATA_DIR, "release_system.db")
SCHEDULER_PID_PATH = os.path.join(DATA_DIR, "scheduler.pid")
SCHEDULER_LOCK_PATH = os.path.join(DATA_DIR, "scheduler.lock")

PRECHECK_THRESHOLDS = {
    "scan_accuracy_min": 0.995,
    "sort_pressure_max": 0.85,
    "scanner_adapt_min": 0.98,
    "center_online_min": 0.95,
}

MONITOR_THRESHOLDS = {
    "scan_fail_rate_max": 0.03,
    "sort_delay_max": 120,
    "loss_rate_max": 0.005,
    "monitor_interval_seconds": 300,
}

APPROVAL_FLOWS = {
    "normal": ["tech_lead", "ops_lead", "hub_manager"],
    "emergency": ["tech_lead", "hub_manager"],
}

APPROVAL_TIMEOUT_MINUTES = {
    "normal": 480,
    "emergency": 60,
}

APPROVAL_TIMEOUT_CHECK_INTERVAL = 300

GRAYSCALE_STAGES = [
    {"name": "pilot", "centers": 1, "wait_hours": 2},
    {"name": "small_batch", "centers": 3, "wait_hours": 4},
    {"name": "medium_batch", "centers": 10, "wait_hours": 6},
    {"name": "full", "centers": -1, "wait_hours": 0},
]

STAKEHOLDERS = {
    "tech_lead": {"name": "张工", "email": "tech@hub.com", "phone": "13800000001"},
    "ops_lead": {"name": "李经理", "email": "ops@hub.com", "phone": "13800000002"},
    "hub_manager": {"name": "王总监", "email": "manager@hub.com", "phone": "13800000003"},
    "quality_lead": {"name": "赵质检", "email": "quality@hub.com", "phone": "13800000004"},
}

DISTRIBUTION_CENTERS = [
    {"id": "DC001", "name": "北京分拨中心", "region": "华北"},
    {"id": "DC002", "name": "上海分拨中心", "region": "华东"},
    {"id": "DC003", "name": "广州分拨中心", "region": "华南"},
    {"id": "DC004", "name": "深圳分拨中心", "region": "华南"},
    {"id": "DC005", "name": "成都分拨中心", "region": "西南"},
    {"id": "DC006", "name": "武汉分拨中心", "region": "华中"},
    {"id": "DC007", "name": "杭州分拨中心", "region": "华东"},
    {"id": "DC008", "name": "西安分拨中心", "region": "西北"},
    {"id": "DC009", "name": "沈阳分拨中心", "region": "东北"},
    {"id": "DC010", "name": "南京分拨中心", "region": "华东"},
    {"id": "DC011", "name": "重庆分拨中心", "region": "西南"},
    {"id": "DC012", "name": "天津分拨中心", "region": "华北"},
]

RELEASE_STATUS_LABELS = {
    "pending": "待检查",
    "precheck_failed": "前置检查失败",
    "awaiting_approval": "待审批",
    "approved": "审批通过",
    "rejected": "审批驳回",
    "grayscale": "灰度发布中",
    "released": "已发布",
    "rolled_back": "已回滚",
}

SUCCESS_STATUSES = {"released"}
FAILED_STATUSES = {"precheck_failed", "rejected", "rolled_back"}
IN_PROGRESS_STATUSES = {"pending", "awaiting_approval", "approved", "grayscale"}

WEEKLY_REPORT_DAY = "monday"
WEEKLY_REPORT_TIME = "09:00"

FREEZE_WINDOWS = [
    {
        "name": "双十一购物节",
        "start": "11-01T00:00:00",
        "end": "11-15T23:59:59",
        "year": None,
        "center_ids": None,
        "level": "block_normal",
        "reason": "双十一大促期间，非紧急发布严禁上线",
    },
    {
        "name": "618购物节",
        "start": "06-15T00:00:00",
        "end": "06-20T23:59:59",
        "year": None,
        "center_ids": None,
        "level": "block_normal",
        "reason": "618大促期间，非紧急发布严禁上线",
    },
    {
        "name": "凌晨分拨高峰(2:00-6:00)",
        "start": None,
        "end": None,
        "year": None,
        "center_ids": None,
        "hour_range": (2, 6),
        "level": "block_normal",
        "reason": "凌晨分拨分拣作业高峰，非紧急发布严禁上线",
    },
]

CENTER_MAINTENANCE_WINDOWS = {
}

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
