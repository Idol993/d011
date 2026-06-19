import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
LOGS_DIR = os.path.join(BASE_DIR, "logs")

DB_PATH = os.path.join(DATA_DIR, "release_system.db")

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

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
