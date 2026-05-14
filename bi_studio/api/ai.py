import frappe

from bi_studio.services.cleanup import delete_ai_analysis_cascade
from bi_studio.services.permissions import ensure_ai_analysis_access, ensure_authenticated, is_admin


@frappe.whitelist()
def get_ai_analyses(search=None):
    ensure_authenticated()
    filters = {}
    if not is_admin():
        filters["owner"] = frappe.session.user
    or_filters = {}
    if search:
        or_filters = {"analysis_name": ["like", f"%{search}%"]}
    rows = frappe.get_list(
        "BI AI Analysis",
        filters=filters,
        or_filters=or_filters,
        fields=["name", "analysis_name", "dashboard", "dataset", "owner", "generated_at", "modified"],
        order_by="generated_at desc",
    )
    favorites = set(
        frappe.get_all(
            "BI Favorite",
            filters={"user": frappe.session.user, "reference_doctype": "BI AI Analysis"},
            pluck="reference_name",
        )
    )
    for row in rows:
        row["dashboard_name"] = frappe.db.get_value("BI Dashboard", row.dashboard, "dashboard_name")
        row["dataset_name"] = frappe.db.get_value("BI Dataset", row.dataset, "dataset_name")
        row["is_favorite"] = row.name in favorites
    return rows


@frappe.whitelist()
def get_ai_analysis_detail(analysis_name):
    analysis = ensure_ai_analysis_access(analysis_name)
    return {
        "analysis": analysis.as_dict(),
        "dashboard_name": frappe.db.get_value("BI Dashboard", analysis.dashboard, "dashboard_name"),
        "dataset_name": frappe.db.get_value("BI Dataset", analysis.dataset, "dataset_name"),
        "is_favorite": bool(
            frappe.db.exists(
                "BI Favorite",
                {
                    "user": frappe.session.user,
                    "reference_doctype": "BI AI Analysis",
                    "reference_name": analysis.name,
                },
            )
        ),
    }


@frappe.whitelist()
def rename_ai_analysis(analysis_name, new_name):
    analysis = ensure_ai_analysis_access(analysis_name, write=True)
    analysis.analysis_name = new_name.strip()
    analysis.save()
    return {"analysis": analysis.name, "analysis_name": analysis.analysis_name}


@frappe.whitelist()
def delete_ai_analysis(analysis_name):
    analysis = ensure_ai_analysis_access(analysis_name, write=True)
    delete_ai_analysis_cascade(analysis.name)
    return {"deleted": True}
