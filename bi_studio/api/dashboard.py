import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from wizio_erp.services.permissions import (
	assert_dashboard_read,
	assert_dashboard_write,
	assert_dataset_read,
	assert_widget_access,
	can_read_dashboard,
	require_admin,
	require_builder,
)
from wizio_erp.utils.data import apply_filters, dataset_rows, dataset_schema, to_json


def _dashboard_payload(doc) -> dict:
	return {
		"name": doc.name,
		"dashboard_name": doc.dashboard_name,
		"dataset": doc.dataset,
		"owner_user": doc.owner_user,
		"status": doc.status,
		"is_ai_generated": int(doc.is_ai_generated or 0),
		"last_refreshed_on": doc.last_refreshed_on,
		"filters": frappe.parse_json(doc.filters_json or "{}") or {},
		"layout": frappe.parse_json(doc.layout_json or "{}") or {},
		"widgets": [row.as_dict() for row in doc.get("widgets") or []],
		"access": [row.as_dict() for row in doc.get("access") or []],
	}


@frappe.whitelist()
def list_dashboards(search: str | None = None, dataset: str | None = None, limit: int = 100):
	require_builder()
	filters = {}
	if dataset:
		filters["dataset"] = dataset
	or_filters = {}
	if search:
		or_filters = {"dashboard_name": ["like", f"%{search}%"], "name": ["like", f"%{search}%"]}
	rows = frappe.get_list(
		"Wizio Dashboard",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "dashboard_name", "dataset", "status", "owner_user", "modified", "last_refreshed_on"],
		order_by="modified desc",
		limit_page_length=int(limit or 100),
	)
	return [row for row in rows if can_read_dashboard(row.name)]


@frappe.whitelist()
def create_dashboard(dataset: str | None = None, dashboard_name: str | None = None, widgets_json: str = "[]"):
	require_builder()
	if dataset:
		assert_dataset_read(dataset)
	doc = frappe.new_doc("Wizio Dashboard")
	doc.dashboard_name = dashboard_name or "Nouveau tableau de bord"
	doc.dataset = dataset
	doc.owner_user = frappe.session.user
	doc.status = "Draft"
	doc.filters_json = "{}"
	doc.layout_json = "{}"
	for row in frappe.parse_json(widgets_json) or []:
		widget = row.get("widget")
		if not widget:
			continue
		assert_widget_access(widget, action="use")
		doc.append(
			"widgets",
			{
				"widget": widget,
				"title": row.get("title"),
				"x": int(row.get("x") or 0),
				"y": int(row.get("y") or 0),
				"width": int(row.get("width") or 4),
				"height": int(row.get("height") or 3),
				"config_json": to_json(row.get("config") or {}),
			},
		)
	doc.insert(ignore_permissions=True)
	return {"dashboard": doc.name}


@frappe.whitelist()
def get_dashboard(dashboard: str, filters_json: str | None = None):
	doc = assert_dashboard_read(dashboard)
	dataset_payload = None
	if doc.dataset:
		dataset_doc = assert_dataset_read(doc.dataset)
		filters = frappe.parse_json(doc.filters_json or "{}") or {}
		filters.update(frappe.parse_json(filters_json or "{}") or {})
		rows = apply_filters(dataset_rows(dataset_doc), filters)
		dataset_payload = {"schema": dataset_schema(dataset_doc), "rows": rows[:200], "total": len(rows)}
	return {"dashboard": _dashboard_payload(doc), "dataset": dataset_payload}


@frappe.whitelist()
def update_dashboard(
	dashboard: str,
	dashboard_name: str | None = None,
	status: str | None = None,
	filters_json: str | None = None,
	layout_json: str | None = None,
	widgets_json: str | None = None,
):
	doc = assert_dashboard_write(dashboard)
	if dashboard_name is not None:
		doc.dashboard_name = dashboard_name.strip()
	if status is not None:
		doc.status = status
	if filters_json is not None:
		json.loads(filters_json or "{}")
		doc.filters_json = filters_json or "{}"
	if layout_json is not None:
		json.loads(layout_json or "{}")
		doc.layout_json = layout_json or "{}"
	if widgets_json is not None:
		doc.set("widgets", [])
		for row in frappe.parse_json(widgets_json) or []:
			widget = row.get("widget")
			if not widget:
				continue
			assert_widget_access(widget, action="use")
			doc.append(
				"widgets",
				{
					"widget": widget,
					"title": row.get("title"),
					"x": int(row.get("x") or 0),
					"y": int(row.get("y") or 0),
					"width": int(row.get("width") or 4),
					"height": int(row.get("height") or 3),
					"config_json": to_json(row.get("config") or {}),
				},
			)
	doc.save(ignore_permissions=True)
	return {"dashboard": doc.name}


@frappe.whitelist()
def delete_dashboard(dashboard: str):
	doc = assert_dashboard_write(dashboard)
	frappe.delete_doc("Wizio Dashboard", doc.name, ignore_permissions=True)
	return {"deleted": True}


@frappe.whitelist()
def duplicate_dashboard(dashboard: str, dashboard_name: str | None = None):
	source = assert_dashboard_read(dashboard)
	copy = frappe.copy_doc(source)
	copy.dashboard_name = dashboard_name or f"{source.dashboard_name} - Copie"
	copy.owner_user = frappe.session.user
	copy.insert(ignore_permissions=True)
	return {"dashboard": copy.name}


@frappe.whitelist()
def refresh_dashboard(dashboard: str):
	doc = assert_dashboard_read(dashboard)
	if not can_read_dashboard(doc.name):
		frappe.throw(_("Acces refuse."))
	doc.last_refreshed_on = now_datetime()
	doc.save(ignore_permissions=True)
	return get_dashboard(doc.name)


@frappe.whitelist()
def export_dashboard(dashboard: str):
	doc = assert_dashboard_read(dashboard)
	content = to_json(get_dashboard(doc.name))
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{doc.dashboard_name}.json",
			"attached_to_doctype": "Wizio Dashboard",
			"attached_to_name": doc.name,
			"content": content,
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	return {"file_url": file_doc.file_url}


@frappe.whitelist()
def grant_dashboard_access(dashboard: str, user: str, can_write: int = 0):
	require_admin()
	doc = frappe.get_doc("Wizio Dashboard", dashboard)
	for row in doc.get("access") or []:
		if row.user == user:
			row.can_read = 1
			row.can_write = int(can_write or 0)
			break
	else:
		doc.append("access", {"user": user, "can_read": 1, "can_write": int(can_write or 0)})
	doc.save(ignore_permissions=True)
	return {"dashboard": doc.name, "user": user}


@frappe.whitelist()
def revoke_dashboard_access(dashboard: str, user: str):
	require_admin()
	doc = frappe.get_doc("Wizio Dashboard", dashboard)
	doc.set("access", [row for row in doc.get("access") or [] if row.user != user])
	doc.save(ignore_permissions=True)
	return {"dashboard": doc.name, "user": user, "revoked": True}


@frappe.whitelist()
def admin_summary():
	require_admin()
	return {
		"datasets": frappe.db.count("Wizio Dataset"),
		"dashboards": frappe.db.count("Wizio Dashboard"),
		"widgets": frappe.db.count("Wizio Widget"),
		"analyses": frappe.db.count("Wizio Analysis"),
		"conversations": frappe.db.count("Wizio AI Conversation"),
	}
