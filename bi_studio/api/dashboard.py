"""API REST pour les tableaux de bord BI Studio.

Toutes les fonctions exposées via @frappe.whitelist() travaillent contre les
doctypes BI* (BI Dashboard, BI Dashboard Widget, BI Dataset, BI AI Analysis).
"""

from __future__ import annotations

import json
from typing import Any

import frappe
from frappe import _
from frappe.utils import now_datetime

from bi_studio.services.cleanup import delete_dashboard_cascade
from bi_studio.services.export import save_dashboard_png
from bi_studio.services.permissions import (
	ensure_authenticated,
	ensure_dashboard_access,
	ensure_dataset_access,
	is_admin,
)
from bi_studio.services.query import get_chart_data, get_dataset_columns, get_kpi_value
from bi_studio.services.serialization import from_json, to_json


def _is_favorite(reference_doctype: str, reference_name: str) -> bool:
	return bool(
		frappe.db.exists(
			"BI Favorite",
			{
				"user": frappe.session.user,
				"reference_doctype": reference_doctype,
				"reference_name": reference_name,
			},
		)
	)


def _dashboard_list_row(row: dict) -> dict:
	row["is_favorite"] = _is_favorite("BI Dashboard", row["name"])
	return row


@frappe.whitelist()
def get_dashboards(search: str | None = None, dataset: str | None = None, limit: int = 100):
	"""Liste les dashboards visibles par l'utilisateur courant."""
	ensure_authenticated()
	filters: dict[str, Any] = {}
	if dataset:
		filters["dataset"] = dataset
	or_filters = {}
	if search:
		or_filters = {
			"dashboard_name": ["like", f"%{search}%"],
			"dashboard_title": ["like", f"%{search}%"],
			"name": ["like", f"%{search}%"],
		}
	rows = frappe.get_list(
		"BI Dashboard",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name",
			"dashboard_name",
			"dashboard_title",
			"description",
			"dataset",
			"dashboard_type",
			"status",
			"quality_score",
			"is_system_suggested",
			"is_user_created",
			"generated_after_import",
			"created_at",
			"modified",
			"owner",
		],
		order_by="modified desc",
		limit_page_length=int(limit or 100),
	)
	return [_dashboard_list_row(row) for row in rows]


# Alias rétrocompatibilité
@frappe.whitelist()
def list_dashboards(search: str | None = None, dataset: str | None = None, limit: int = 100):
	return get_dashboards(search=search, dataset=dataset, limit=limit)


def _widget_to_payload(widget) -> dict:
	config = from_json(widget.get("config_json"), {}) if isinstance(widget, dict) else from_json(widget.config_json, {})
	options = from_json(widget.get("options_json"), {}) if isinstance(widget, dict) else from_json(widget.options_json, {})
	position = from_json(widget.get("position_json"), {}) if isinstance(widget, dict) else from_json(widget.position_json, {})
	base = widget.as_dict() if hasattr(widget, "as_dict") else dict(widget)
	payload = {
		"id": (config.get("id") if isinstance(config, dict) else None) or base.get("name"),
		"widget_name": base.get("widget_name"),
		"title": (config.get("title") if isinstance(config, dict) else None) or base.get("widget_name"),
		"widget_type": base.get("widget_type"),
		"chart_type": base.get("chart_type"),
		"value_field": base.get("value_field"),
		"category_field": base.get("category_field"),
		"calculation_type": base.get("calculation_type"),
		"display_order": base.get("display_order"),
		"visible": int(base.get("visible") or 0),
		"top_n": (config.get("top_n") if isinstance(config, dict) else None),
		"options": options if isinstance(options, dict) else {},
		"position": position if isinstance(position, dict) else {},
		"config": config if isinstance(config, dict) else {},
	}
	return payload


def _compute_widget_value(dataset_doc, widget_payload: dict) -> dict:
	"""Calcule les données (KPI/Chart) du widget si possible. Renvoie un dict enrichi."""
	enriched = dict(widget_payload)
	if not dataset_doc:
		return enriched
	wtype = (widget_payload.get("widget_type") or "").strip()
	try:
		if wtype in {"KPI", "Quality Card"}:
			enriched["value"] = get_kpi_value(dataset_doc, widget_payload)
		elif wtype in {"Chart", "Bar Chart", "Line Chart", "Pie Chart", "Donut Chart", "Histogram", "Scatter Plot"}:
			enriched["data"] = get_chart_data(dataset_doc, widget_payload)
	except Exception as error:
		enriched["error"] = str(error)
	return enriched


def _latest_analysis(dashboard_name: str) -> dict | None:
	rows = frappe.get_all(
		"BI AI Analysis",
		filters={"dashboard": dashboard_name},
		fields=["name", "analysis_name", "summary_short", "summary_detailed", "conclusion", "generated_at"],
		order_by="generated_at desc",
		limit=1,
	)
	return rows[0] if rows else None


@frappe.whitelist()
def get_dashboard_detail(dashboard_name: str):
	"""Charge un dashboard avec ses widgets, son dataset et la dernière analyse IA."""
	dashboard = ensure_dashboard_access(dashboard_name)
	dataset_doc = None
	dataset_payload = {"name": None, "dataset_name": None}
	columns: list = []
	if dashboard.dataset:
		try:
			dataset_doc = ensure_dataset_access(dashboard.dataset)
			dataset_payload = {
				"name": dataset_doc.name,
				"dataset_name": dataset_doc.dataset_name,
				"row_count": dataset_doc.row_count,
				"column_count": dataset_doc.column_count,
				"status": dataset_doc.status,
				"clean_table": dataset_doc.clean_table,
			}
			columns = get_dataset_columns(dataset_doc.name)
		except frappe.PermissionError:
			dataset_doc = None

	widgets_payload = []
	for widget in dashboard.get("widgets") or []:
		payload = _widget_to_payload(widget)
		widgets_payload.append(_compute_widget_value(dataset_doc, payload))

	return {
		"dashboard": {
			"name": dashboard.name,
			"dashboard_name": dashboard.dashboard_name,
			"dashboard_title": dashboard.dashboard_title,
			"description": dashboard.description,
			"dataset": dashboard.dataset,
			"dashboard_type": dashboard.dashboard_type,
			"status": dashboard.status,
			"quality_score": dashboard.quality_score,
			"is_system_suggested": int(dashboard.is_system_suggested or 0),
			"is_user_created": int(dashboard.is_user_created or 0),
			"generated_after_import": int(dashboard.generated_after_import or 0),
			"created_at": dashboard.created_at,
			"filters": from_json(dashboard.filters_json, {}) or {},
			"layout": from_json(dashboard.layout_json, {}) or {},
			"ai_summary": dashboard.ai_summary,
			"owner": dashboard.owner,
			"modified": dashboard.modified,
		},
		"dataset": dataset_payload,
		"widgets": widgets_payload,
		"columns": columns,
		"is_favorite": _is_favorite("BI Dashboard", dashboard.name),
		"latest_analysis": _latest_analysis(dashboard.name),
	}


@frappe.whitelist()
def get_dashboard(dashboard: str):
	"""Alias rétrocompatibilité."""
	return get_dashboard_detail(dashboard)


def _widgets_from_json(widgets_json: str | None) -> list[dict]:
	parsed = from_json(widgets_json, [])
	if not isinstance(parsed, list):
		frappe.throw(_("Les widgets doivent être une liste JSON."))
	return parsed


def _apply_widgets_to_dashboard(doc, widgets: list[dict]) -> None:
	doc.set("widgets", [])
	for index, widget in enumerate(widgets):
		if not isinstance(widget, dict):
			continue
		widget_type = widget.get("widget_type") or "KPI"
		chart_type = widget.get("chart_type")
		doc.append(
			"widgets",
			{
				"widget_name": widget.get("title") or widget.get("widget_name") or f"Widget {index + 1}",
				"widget_type": widget_type,
				"chart_type": chart_type,
				"value_field": widget.get("value_field"),
				"category_field": widget.get("category_field"),
				"calculation_type": widget.get("calculation_type"),
				"display_order": widget.get("display_order") if widget.get("display_order") is not None else index,
				"visible": 1 if widget.get("visible", True) else 0,
				"filters_json": to_json(widget.get("filters") or {}),
				"options_json": to_json(widget.get("options") or {}),
				"config_json": to_json(widget),
				"position_json": to_json(widget.get("position") or {}),
			},
		)


def _validate_json_string(value: str | None) -> str:
	if value is None:
		return "{}"
	try:
		json.loads(value or "{}")
	except (TypeError, ValueError):
		frappe.throw(_("JSON invalide."))
	return value or "{}"


@frappe.whitelist()
def create_dashboard(
	dashboard_name: str | None = None,
	dashboard_title: str | None = None,
	dataset_name: str | None = None,
	dataset: str | None = None,
	widgets_json: str = "[]",
	layout_json: str = "{}",
	filters_json: str = "{}",
	description: str | None = None,
):
	ensure_authenticated()
	dataset_id = dataset_name or dataset
	if not dataset_id:
		frappe.throw(_("Un dataset est requis pour créer un dashboard."))
	ensure_dataset_access(dataset_id)
	title = (dashboard_title or dashboard_name or "Nouveau dashboard").strip()
	doc = frappe.new_doc("BI Dashboard")
	doc.dashboard_name = (dashboard_name or dashboard_title or "Nouveau dashboard").strip()
	doc.dashboard_title = title
	doc.dashboard_type = "User Created"
	doc.status = "Draft"
	doc.is_user_created = 1
	doc.dataset = dataset_id
	doc.description = description
	doc.created_at = now_datetime()
	doc.filters_json = _validate_json_string(filters_json)
	doc.layout_json = _validate_json_string(layout_json)
	doc.widgets_json = widgets_json or "[]"
	_apply_widgets_to_dashboard(doc, _widgets_from_json(widgets_json))
	doc.insert(ignore_permissions=is_admin())
	return {"dashboard": doc.name}


@frappe.whitelist()
def update_dashboard(
	dashboard_name: str,
	dashboard_title: str | None = None,
	dataset_name: str | None = None,
	dataset: str | None = None,
	widgets_json: str | None = None,
	layout_json: str | None = None,
	filters_json: str | None = None,
	description: str | None = None,
	status: str | None = None,
):
	doc = ensure_dashboard_access(dashboard_name, ptype="write")
	dataset_id = dataset_name or dataset
	if dataset_id and dataset_id != doc.dataset:
		ensure_dataset_access(dataset_id)
		doc.dataset = dataset_id
	if dashboard_title is not None:
		doc.dashboard_title = dashboard_title.strip()
	if description is not None:
		doc.description = description
	if status is not None:
		doc.status = status
	if filters_json is not None:
		doc.filters_json = _validate_json_string(filters_json)
	if layout_json is not None:
		doc.layout_json = _validate_json_string(layout_json)
	if widgets_json is not None:
		doc.widgets_json = widgets_json or "[]"
		_apply_widgets_to_dashboard(doc, _widgets_from_json(widgets_json))
	doc.save(ignore_permissions=is_admin())
	return {"dashboard": doc.name}


@frappe.whitelist()
def rename_dashboard(dashboard_name: str, new_name: str):
	doc = ensure_dashboard_access(dashboard_name, ptype="write")
	clean = (new_name or "").strip()
	if not clean:
		frappe.throw(_("Nouveau nom requis."))
	doc.dashboard_name = clean
	doc.dashboard_title = clean
	doc.save(ignore_permissions=is_admin())
	return {"dashboard": doc.name, "dashboard_name": doc.dashboard_name}


@frappe.whitelist()
def delete_dashboard(dashboard_name: str):
	ensure_dashboard_access(dashboard_name, ptype="delete")
	delete_dashboard_cascade(dashboard_name)
	return {"deleted": True, "dashboard_name": dashboard_name}


@frappe.whitelist()
def export_dashboard_png(dashboard_name: str, image_data: str):
	ensure_dashboard_access(dashboard_name)
	return save_dashboard_png(dashboard_name, image_data)
