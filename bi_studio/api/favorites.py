import frappe
from frappe import _
from frappe.utils import now_datetime

from bi_studio.services.permissions import (
    ensure_ai_analysis_access,
    ensure_authenticated,
    ensure_dashboard_access,
    ensure_dataset_access,
)


SUPPORTED_TYPES = {
    "BI Dataset": ensure_dataset_access,
    "BI Dashboard": ensure_dashboard_access,
    "BI AI Analysis": ensure_ai_analysis_access,
}


@frappe.whitelist()
def toggle_favorite(reference_doctype, reference_name):
    ensure_authenticated()
    if reference_doctype not in SUPPORTED_TYPES:
        frappe.throw(_("Type de favori non supporté."))
    SUPPORTED_TYPES[reference_doctype](reference_name)

    existing = frappe.db.get_value(
        "BI Favorite",
        {"user": frappe.session.user, "reference_doctype": reference_doctype, "reference_name": reference_name},
        "name",
    )
    if existing:
        frappe.delete_doc("BI Favorite", existing, ignore_permissions=True)
        return {"is_favorite": False}

    label = get_reference_label(reference_doctype, reference_name)
    favorite = frappe.get_doc(
        {
            "doctype": "BI Favorite",
            "user": frappe.session.user,
            "reference_doctype": reference_doctype,
            "reference_name": reference_name,
            "label": label,
            "favorited_at": now_datetime(),
        }
    )
    favorite.insert(ignore_permissions=True)
    return {"is_favorite": True}


@frappe.whitelist()
def get_favorites():
    ensure_authenticated()
    rows = frappe.get_list(
        "BI Favorite",
        filters={"user": frappe.session.user},
        fields=["name", "reference_doctype", "reference_name", "label", "owner", "favorited_at"],
        order_by="favorited_at desc",
    )
    for row in rows:
        row["route"] = get_reference_route(row.reference_doctype, row.reference_name)
        row["reference_owner"] = frappe.db.get_value(row.reference_doctype, row.reference_name, "owner")
        row["size_value"], row["size_label"] = get_reference_size(row.reference_doctype, row.reference_name)
    return rows


def get_reference_label(reference_doctype, reference_name):
    field = {
        "BI Dataset": "dataset_name",
        "BI Dashboard": "dashboard_name",
        "BI AI Analysis": "analysis_name",
    }[reference_doctype]
    return frappe.db.get_value(reference_doctype, reference_name, field) or reference_name


def get_reference_route(reference_doctype, reference_name):
    if reference_doctype == "BI Dataset":
        return f"/desk/bi_studio/dataset/{reference_name}"
    if reference_doctype == "BI Dashboard":
        return f"/desk/bi_studio/dashboard/{reference_name}"
    return f"/desk/bi_studio/ai-analysis/{reference_name}"


def get_reference_size(reference_doctype, reference_name):
    if reference_doctype == "BI Dataset":
        row_count = frappe.db.get_value("BI Dataset", reference_name, "row_count") or 0
        return int(row_count), _("{0} lignes").format(row_count)
    if reference_doctype == "BI Dashboard":
        widget_count = frappe.db.count(
            "BI Dashboard Widget",
            {"parent": reference_name, "parenttype": "BI Dashboard"},
        )
        return int(widget_count), _("{0} widgets").format(widget_count)
    return 0, _("Analyse IA")
