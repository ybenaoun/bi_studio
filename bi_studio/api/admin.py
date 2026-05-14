from collections import Counter, defaultdict

import frappe
from frappe.utils import add_days, getdate, nowdate

from bi_studio.services.cleanup import delete_ai_analysis_cascade, delete_dashboard_cascade, delete_dataset_cascade
from bi_studio.services.permissions import ensure_admin


@frappe.whitelist()
def get_admin_dashboard_summary():
    ensure_admin()
    total_datasets = frappe.db.count("BI Dataset")
    total_dashboards = frappe.db.count("BI Dashboard")
    total_ai_analyses = frappe.db.count("BI AI Analysis")
    top_ai_user = get_top_ai_user(total_ai_analyses)
    imports_summary = get_imports_over_time_summary()
    used_datasets = frappe.db.sql(
        "SELECT COUNT(DISTINCT dataset) AS total FROM `tabBI Dashboard` WHERE dataset IS NOT NULL",
        as_dict=True,
    )[0].total or 0

    return {
        "total_datasets": total_datasets,
        "total_dashboards": total_dashboards,
        "total_ai_analyses": total_ai_analyses,
        "top_ai_user": top_ai_user,
        "imports_over_time_summary": imports_summary,
        "dataset_usage_rate": {
            "used": used_datasets,
            "unused": max(total_datasets - used_datasets, 0),
            "percentage": round((used_datasets / total_datasets) * 100, 1) if total_datasets else 0,
        },
    }


def get_top_ai_user(total_ai_analyses):
    if not total_ai_analyses:
        return {"user": None, "total_ai_analyses": 0, "percentage": 0}
    rows = frappe.get_all("BI AI Analysis", fields=["owner"])
    counter = Counter(row.owner for row in rows)
    user, total = counter.most_common(1)[0]
    return {
        "user": user,
        "total_ai_analyses": total,
        "percentage": round((total / total_ai_analyses) * 100, 1),
    }


def get_imports_over_time_summary():
    today = getdate(nowdate())
    week_start = add_days(today, -6)
    month_start = today.replace(day=1)
    rows = frappe.get_all("BI Import Job", fields=["started_at", "creation"])
    dates = [getdate(row.started_at or row.creation) for row in rows]
    counter = Counter(dates)
    peak = counter.most_common(1)[0][0].isoformat() if counter else None
    return {
        "imports_today": sum(1 for date in dates if date == today),
        "imports_this_week": sum(1 for date in dates if date >= week_start),
        "imports_this_month": sum(1 for date in dates if date >= month_start),
        "peak_period": peak,
    }


@frappe.whitelist()
def get_imports_over_time(group_by="day", date_from=None, date_to=None):
    ensure_admin()
    date_from = getdate(date_from) if date_from else add_days(getdate(nowdate()), -30)
    date_to = getdate(date_to) if date_to else getdate(nowdate())
    rows = frappe.get_all(
        "BI Import Job",
        filters={"started_at": ["between", [date_from, date_to]]},
        fields=["started_at", "status"],
        order_by="started_at asc",
    )
    buckets = defaultdict(int)
    for row in rows:
        date = getdate(row.started_at)
        if group_by == "month":
            key = date.strftime("%Y-%m")
        elif group_by == "week":
            key = f"{date.isocalendar().year}-W{date.isocalendar().week:02d}"
        else:
            key = date.isoformat()
        buckets[key] += 1
    return [{"period": key, "imports": value} for key, value in sorted(buckets.items())]


@frappe.whitelist()
def get_all_datasets():
    ensure_admin()
    return frappe.get_all(
        "BI Dataset",
        fields=["name", "dataset_name", "status", "owner", "row_count", "column_count", "imported_at", "modified"],
        order_by="modified desc",
    )


@frappe.whitelist()
def get_all_dashboards():
    ensure_admin()
    return frappe.get_all(
        "BI Dashboard",
        fields=["name", "dashboard_name", "dataset", "dashboard_type", "owner", "created_at", "modified"],
        order_by="modified desc",
    )


@frappe.whitelist()
def get_all_ai_analyses():
    ensure_admin()
    return frappe.get_all(
        "BI AI Analysis",
        fields=["name", "analysis_name", "dashboard", "dataset", "owner", "generated_at", "modified"],
        order_by="generated_at desc",
    )


@frappe.whitelist()
def get_recent_imports(limit=50):
    ensure_admin()
    return frappe.get_all(
        "BI Import Job",
        fields=["name", "dataset", "source_file", "imported_by", "status", "started_at", "finished_at", "row_count", "column_count", "error_message"],
        order_by="creation desc",
        limit=int(limit),
    )


@frappe.whitelist()
def admin_delete_dataset(dataset_name):
    ensure_admin()
    delete_dataset_cascade(dataset_name)
    return {"deleted": True}


@frappe.whitelist()
def admin_delete_dashboard(dashboard_name):
    ensure_admin()
    delete_dashboard_cascade(dashboard_name)
    return {"deleted": True}


@frappe.whitelist()
def admin_delete_ai_analysis(analysis_name):
    ensure_admin()
    delete_ai_analysis_cascade(analysis_name)
    return {"deleted": True}
