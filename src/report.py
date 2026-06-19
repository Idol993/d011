import os
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import platform
    if platform.system() == "Windows":
        matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial Unicode MS"]
    elif platform.system() == "Darwin":
        matplotlib.rcParams["font.sans-serif"] = ["PingFang SC", "Arial Unicode MS"]
    else:
        matplotlib.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "WenQuanYi Micro Hei"]
    matplotlib.rcParams["axes.unicode_minus"] = False
except Exception:
    plt = None

try:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image
    from reportlab.lib.units import cm
except Exception:
    SimpleDocTemplate = None

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
except Exception:
    Workbook = None

from .config import REPORTS_DIR
from .logger import get_logger
from . import database as db
from . import notifier

logger = get_logger("report")


def _get_week_range(ref_date: Optional[datetime] = None) -> Tuple[str, str, datetime, datetime]:
    ref = ref_date or datetime.now()
    start = ref - timedelta(days=ref.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"), start, end


def collect_weekly_stats(ref_date: Optional[datetime] = None) -> Dict:
    start_str, end_str, start_dt, end_dt = _get_week_range(ref_date)
    filters = {"start_time": start_str, "end_time": end_str}
    releases = db.list_releases(filters)

    release_total = len(releases)
    release_success = len([r for r in releases
                           if r["status"] in ("released", "grayscale", "approved")
                           and not r["rollback_triggered"]])
    rollback_count = len([r for r in releases if r["rollback_triggered"]])

    durations = []
    for r in releases:
        d = db.get_approval_duration_seconds(r["id"])
        if d is not None:
            durations.append(d)
    avg_approval = sum(durations) / len(durations) if durations else 0.0

    status_counts = {}
    risk_counts = {}
    for r in releases:
        status_counts[r["status"]] = status_counts.get(r["status"], 0) + 1
        risk_counts[r["risk_level"]] = risk_counts.get(r["risk_level"], 0) + 1

    daily_stats = {}
    for i in range(7):
        day = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        daily_stats[day] = {"releases": 0, "rollbacks": 0}
    for r in releases:
        try:
            r_day = r["created_at"][:10]
            if r_day in daily_stats:
                daily_stats[r_day]["releases"] += 1
            if r["rollback_triggered"] and r_day in daily_stats:
                daily_stats[r_day]["rollbacks"] += 1
        except Exception:
            pass

    return {
        "week_start": start_str,
        "week_end": end_str,
        "start_dt": start_dt,
        "end_dt": end_dt,
        "release_total": release_total,
        "release_success": release_success,
        "release_success_rate": (release_success / release_total) if release_total else 0.0,
        "rollback_count": rollback_count,
        "avg_approval_seconds": avg_approval,
        "releases": releases,
        "status_counts": status_counts,
        "risk_counts": risk_counts,
        "daily_stats": daily_stats,
    }


def _make_chart(stats: Dict, out_path: str) -> Optional[str]:
    if plt is None:
        return None
    try:
        days = list(stats["daily_stats"].keys())
        releases = [s["releases"] for s in stats["daily_stats"].values()]
        rollbacks = [s["rollbacks"] for s in stats["daily_stats"].values()]

        fig, axes = plt.subplots(1, 2, figsize=(12, 4))
        x = range(len(days))
        short_days = [d[5:] for d in days]

        axes[0].bar(x, releases, label="发布数", color="#4C72B0", alpha=0.85)
        axes[0].bar(x, rollbacks, bottom=releases, label="回滚数", color="#DD8452", alpha=0.85)
        axes[0].set_xticks(list(x))
        axes[0].set_xticklabels(short_days, rotation=30)
        axes[0].set_title("每日发布与回滚趋势")
        axes[0].legend()
        axes[0].grid(axis="y", linestyle="--", alpha=0.4)

        labels = ["成功发布", "触发回滚"]
        values = [stats["release_success"], stats["rollback_count"]]
        if sum(values) == 0:
            values = [1, 0]
        colors_pie = ["#55A868", "#C44E52"]
        axes[1].pie(values, labels=labels, autopct="%1.1f%%", colors=colors_pie, startangle=90)
        axes[1].set_title("发布结果分布")

        plt.tight_layout()
        plt.savefig(out_path, dpi=120)
        plt.close(fig)
        return out_path
    except Exception as e:
        logger.error(f"生成图表失败: {e}")
        return None


def _make_pdf(stats: Dict, chart_path: Optional[str], out_path: str) -> Optional[str]:
    if SimpleDocTemplate is None:
        return None
    try:
        doc = SimpleDocTemplate(out_path, pagesize=A4)
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "TitleCN", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=18
        )
        h2 = ParagraphStyle(
            "H2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13
        )
        body = ParagraphStyle(
            "Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=10, leading=14
        )
        story = []
        story.append(Paragraph("快递快运分拨系统 - 发布与回滚周报", title_style))
        story.append(Spacer(1, 0.3 * cm))
        story.append(Paragraph(
            f"统计周期: {stats['week_start']} ~ {stats['week_end']}", body
        ))
        story.append(Spacer(1, 0.4 * cm))

        story.append(Paragraph("一、核心指标", h2))
        story.append(Spacer(1, 0.2 * cm))
        rate = stats["release_success_rate"] * 100
        avg_min = stats["avg_approval_seconds"] / 60
        summary_data = [
            ["指标", "数值"],
            ["发布总数", str(stats["release_total"])],
            ["成功发布数", str(stats["release_success"])],
            ["发布成功率", f"{rate:.2f}%"],
            ["回滚次数", str(stats["rollback_count"])],
            ["平均审批时长", f"{avg_min:.2f} 分钟"],
        ]
        t = Table(summary_data, colWidths=[8 * cm, 5 * cm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4C72B0")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.4 * cm))

        story.append(Paragraph("二、趋势图表", h2))
        story.append(Spacer(1, 0.2 * cm))
        if chart_path and os.path.exists(chart_path):
            story.append(Image(chart_path, width=16 * cm, height=5.5 * cm))
        else:
            story.append(Paragraph("(图表生成失败)", body))
        story.append(Spacer(1, 0.4 * cm))

        story.append(Paragraph("三、发布明细", h2))
        story.append(Spacer(1, 0.2 * cm))
        detail_header = ["版本", "风险", "状态", "提交者", "提交时间"]
        detail_data = [detail_header]
        for r in stats["releases"][:15]:
            detail_data.append([
                r["version"], r["risk_level"], r["status"],
                r["submitter"], r["created_at"],
            ])
        if len(detail_data) == 1:
            detail_data.append(["(无数据)", "", "", "", ""])
        dt = Table(detail_data, colWidths=[3.5 * cm, 2 * cm, 2.5 * cm, 2.5 * cm, 4.5 * cm])
        dt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#55A868")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(dt)

        doc.build(story)
        return out_path
    except Exception as e:
        logger.error(f"生成PDF失败: {e}")
        return None


def _make_excel(stats: Dict, out_path: str) -> Optional[str]:
    if Workbook is None:
        return None
    try:
        wb = Workbook()

        ws1 = wb.active
        ws1.title = "核心指标"
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill("solid", fgColor="4C72B0")
        center = Alignment(horizontal="center", vertical="center")

        ws1.append(["指标", "数值"])
        for c in ws1[1]:
            c.font = header_font
            c.fill = header_fill
            c.alignment = center
        rate = stats["release_success_rate"] * 100
        avg_min = stats["avg_approval_seconds"] / 60
        rows = [
            ["发布总数", stats["release_total"]],
            ["成功发布数", stats["release_success"]],
            ["发布成功率", f"{rate:.2f}%"],
            ["回滚次数", stats["rollback_count"]],
            ["平均审批时长(分钟)", f"{avg_min:.2f}"],
            ["统计开始时间", stats["week_start"]],
            ["统计结束时间", stats["week_end"]],
        ]
        for r in rows:
            ws1.append(r)
        for row in ws1.iter_rows():
            for c in row:
                c.alignment = center
        ws1.column_dimensions["A"].width = 25
        ws1.column_dimensions["B"].width = 30

        ws2 = wb.create_sheet("每日统计")
        ws2.append(["日期", "发布数", "回滚数"])
        for c in ws2[1]:
            c.font = header_font
            c.fill = header_fill
            c.alignment = center
        for day, s in stats["daily_stats"].items():
            ws2.append([day, s["releases"], s["rollbacks"]])
        ws2.column_dimensions["A"].width = 15
        ws2.column_dimensions["B"].width = 12
        ws2.column_dimensions["C"].width = 12

        ws3 = wb.create_sheet("发布明细")
        headers = ["版本号", "风险级别", "状态", "提交人", "描述",
                   "稳定版本", "是否触发回滚", "回滚原因", "创建时间", "更新时间"]
        ws3.append(headers)
        for c in ws3[1]:
            c.font = header_font
            c.fill = header_fill
            c.alignment = center
        for r in stats["releases"]:
            ws3.append([
                r["version"], r["risk_level"], r["status"], r["submitter"],
                r.get("description") or "",
                r.get("stable_version") or "",
                "是" if r["rollback_triggered"] else "否",
                r.get("rollback_reason") or "",
                r["created_at"], r["updated_at"],
            ])
        for col, w in zip("ABCDEFGHIJ", [18, 12, 16, 12, 30, 15, 14, 30, 22, 22]):
            ws3.column_dimensions[col].width = w

        ws4 = wb.create_sheet("回滚明细")
        rb_headers = ["回滚ID", "发布ID", "影响分拨", "异常件量", "原因",
                      "回滚到版本", "状态", "创建时间", "完成时间"]
        ws4.append(rb_headers)
        for c in ws4[1]:
            c.font = header_font
            c.fill = header_fill
            c.alignment = center
        rollbacks = db.list_rollbacks()
        ws4.column_dimensions["A"].width = 10
        ws4.column_dimensions["B"].width = 10
        ws4.column_dimensions["C"].width = 25
        ws4.column_dimensions["D"].width = 12
        ws4.column_dimensions["E"].width = 35
        ws4.column_dimensions["F"].width = 18
        ws4.column_dimensions["G"].width = 14
        ws4.column_dimensions["H"].width = 22
        ws4.column_dimensions["I"].width = 22
        for rb in rollbacks:
            created = rb["created_at"] or ""
            if stats["week_start"] <= created <= stats["week_end"]:
                ws4.append([
                    rb["id"], rb["release_id"],
                    ", ".join(rb["affected_centers"]),
                    rb["affected_parcels"], rb["reason"],
                    rb["rolled_back_version"], rb["status"],
                    rb["created_at"], rb.get("completed_at") or "",
                ])

        wb.save(out_path)
        return out_path
    except Exception as e:
        logger.error(f"生成Excel失败: {e}")
        return None


def generate_weekly_report(ref_date: Optional[datetime] = None) -> Dict:
    stats = collect_weekly_stats(ref_date)
    tag = stats["start_dt"].strftime("%Y%m%d")
    chart_path = os.path.join(REPORTS_DIR, f"weekly_chart_{tag}.png")
    pdf_path = os.path.join(REPORTS_DIR, f"weekly_report_{tag}.pdf")
    excel_path = os.path.join(REPORTS_DIR, f"weekly_report_{tag}.xlsx")

    chart = _make_chart(stats, chart_path)
    pdf = _make_pdf(stats, chart, pdf_path)
    xls = _make_excel(stats, excel_path)

    report_id = db.insert_weekly_report(
        stats["week_start"], stats["week_end"],
        stats["release_total"], stats["release_success"],
        stats["rollback_count"], stats["avg_approval_seconds"],
        pdf, xls,
    )
    if pdf and xls:
        notifier.notify_weekly_report_ready(report_id, pdf, xls)

    return {
        "report_id": report_id,
        "stats": stats,
        "pdf": pdf,
        "excel": xls,
        "chart": chart,
    }
