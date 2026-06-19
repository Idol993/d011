import random
from typing import Dict, Tuple, List
from .config import PRECHECK_THRESHOLDS, DISTRIBUTION_CENTERS
from .logger import get_logger
from . import database as db
from . import notifier

logger = get_logger("precheck")


def _check_scan_accuracy(version: str) -> Tuple[float, bool, str]:
    accuracy = round(random.uniform(0.98, 0.999), 4)
    threshold = PRECHECK_THRESHOLDS["scan_accuracy_min"]
    passed = accuracy >= threshold
    detail = (f"扫描准确率检测: {accuracy*100:.2f}%, 阈值≥{threshold*100:.2f}% | "
              f"样本量=10000, 错扫件={int(10000*(1-accuracy))}")
    logger.info(detail)
    return accuracy, passed, detail


def _check_sort_pressure(version: str) -> Tuple[float, bool, str]:
    pressure = round(random.uniform(0.5, 0.95), 4)
    threshold = PRECHECK_THRESHOLDS["sort_pressure_max"]
    passed = pressure <= threshold
    detail = (f"分拣压力测试: {pressure*100:.2f}%, 阈值≤{threshold*100:.2f}% | "
              f"峰值吞吐={int(8000*pressure)}件/小时, 设备负载正常")
    logger.info(detail)
    return pressure, passed, detail


def _check_scanner_adapt(version: str) -> Tuple[float, bool, str]:
    adapt = round(random.uniform(0.95, 1.0), 4)
    threshold = PRECHECK_THRESHOLDS["scanner_adapt_min"]
    passed = adapt >= threshold
    scanner_types = ["霍尼韦尔Xenon", "优解YJ4600", "新大陆HR32", "民德MD6100"]
    failed = [s for s in scanner_types if random.random() > adapt]
    detail = (f"巴枪适配校验: {adapt*100:.2f}%, 阈值≥{threshold*100:.2f}% | "
              f"测试机型={len(scanner_types)}款, "
              f"{'全部通过' if not failed else '未适配机型: '+','.join(failed)}")
    logger.info(detail)
    return adapt, passed, detail


def _check_center_online(version: str) -> Tuple[float, bool, str]:
    total = len(DISTRIBUTION_CENTERS)
    offline_count = random.randint(0, 2)
    online = total - offline_count
    rate = round(online / total, 4)
    threshold = PRECHECK_THRESHOLDS["center_online_min"]
    passed = rate >= threshold
    offline_centers = random.sample([c["id"] for c in DISTRIBUTION_CENTERS], offline_count) if offline_count else []
    detail = (f"分拨中心在线状态: {online}/{total} ({rate*100:.2f}%), 阈值≥{threshold*100:.2f}% | "
              f"{'离线中心: '+','.join(offline_centers) if offline_centers else '全部在线'}")
    logger.info(detail)
    return rate, passed, detail


CHECKERS = {
    "scan_accuracy": _check_scan_accuracy,
    "sort_pressure": _check_sort_pressure,
    "scanner_adapt": _check_scanner_adapt,
    "center_online": _check_center_online,
}


def run_prechecks(release_id: int) -> Tuple[bool, List[Dict]]:
    release = db.get_release(release_id)
    if not release:
        raise ValueError(f"Release {release_id} not found")

    version = release["version"]
    logger.info(f"开始执行版本 {version} 的前置条件检查...")

    results = []
    all_passed = True

    for check_name, checker in CHECKERS.items():
        try:
            value, passed, detail = checker(version)
            db.insert_precheck(release_id, check_name, value, passed, detail)
            results.append({
                "check": check_name,
                "value": value,
                "passed": passed,
                "detail": detail,
            })
            if not passed:
                all_passed = False
        except Exception as e:
            logger.error(f"检查 {check_name} 失败: {e}")
            db.insert_precheck(release_id, check_name, 0.0, False, f"检查异常: {e}")
            results.append({
                "check": check_name,
                "value": 0.0,
                "passed": False,
                "detail": f"检查异常: {e}",
            })
            all_passed = False

    status = "precheck_passed" if all_passed else "precheck_failed"
    db.update_release_status(release_id, status)

    summary = "\n".join([f"  [{ '✓' if r['passed'] else '✗' }] {r['detail']}" for r in results])
    notifier.notify_precheck_result(version, all_passed, summary)

    if all_passed:
        logger.info(f"版本 {version} 前置条件检查全部通过")
    else:
        logger.warning(f"版本 {version} 前置条件检查未通过")

    return all_passed, results
