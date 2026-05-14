"""Build a BI Dashboard + BI Widget records from a validated AI spec.

Cohere never produces final values: this module computes them from the cleaned
dataset and persists them in BI Widget rows.
"""
from __future__ import annotations

import json
import traceback
from typing import Any

import frappe
import pandas as pd
from frappe.utils import now_datetime

from bi_studio.api.dashboard_renderer_data import (
    calculate_chart_data,
    calculate_filter_data,
    calculate_kpi,
    calculate_table_data,
)
from bi_studio.utils.json_schema import ALLOWED_WIDGET_TYPES


FRAPPE_WIDGET_TYPE_BY_DASHBOARD_V1 = {
    "kpi_card": "kpi",
    "bar_chart": "bar",
    "line_chart": "line",
    "pie_chart": "pie",
    "data_table": "table",
    # BI Widget.widget_type is a legacy Select field. The public type remains
    # "filter" in config_json; the stored value only satisfies Frappe validation.
    "filter": "table",
}


def _stored_widget_type(widget_type: str) -> str:
    return FRAPPE_WIDGET_TYPE_BY_DASHBOARD_V1.get(widget_type, widget_type)


def _public_widget_type(stored_widget_type: str | None, config: dict[str, Any]) -> str | None:
    config_type = config.get("type") if isinstance(config, dict) else None
    if config_type in ALLOWED_WIDGET_TYPES:
        return config_type
    return stored_widget_type


def _load_spec(spec_doc: Any) -> dict[str, Any]:
    raw = spec_doc.validated_json or ""
    if not raw:
        raise ValueError("DashboardDefinition validé manquant: le renderer refuse la réponse IA brute.")
    try:
        spec = json.loads(raw)
    except Exception as exc:
        raise ValueError("DashboardDefinition validé invalide: JSON non parsable.") from exc
    if not isinstance(spec, dict) or spec.get("schema_version") != "dashboard.v1":
        raise ValueError("DashboardDefinition validé invalide: schema_version dashboard.v1 requis.")
    return spec


def _load_df(clean_dataset_doc: Any) -> pd.DataFrame:
    raw = clean_dataset_doc.clean_data_json or "[]"
    try:
        records = json.loads(raw)
    except Exception:
        records = []
    return pd.DataFrame(records)


def _make_widget(
    dashboard_name: str,
    widget_id: str,
    widget_type: str,
    title: str,
    description: str,
    config: dict[str, Any],
    data: dict[str, Any],
    position: dict[str, Any] | None = None,
) -> Any:
    widget = frappe.new_doc("BI Widget")
    widget.dashboard = dashboard_name
    widget.widget_id = widget_id
    widget.widget_type = _stored_widget_type(widget_type)
    widget.title = title or widget_id
    widget.description = description or ""
    widget.config_json = json.dumps(config, ensure_ascii=False, default=str)
    widget.data_json = json.dumps(data, ensure_ascii=False, default=str)
    widget.position_json = json.dumps(position or {}, ensure_ascii=False)
    widget.computed_on = now_datetime()
    if "error" in data:
        widget.compute_status = "Failed"
        widget.compute_error = str(data.get("error"))[:240]
    else:
        widget.compute_status = "Success"
    widget.insert(ignore_permissions=True)
    return widget


def build_dashboard_from_spec(clean_dataset_doc: Any, spec_doc: Any) -> Any:
    """Create a BI Dashboard + its BI Widget rows from the validated spec."""
    spec = _load_spec(spec_doc)
    df = _load_df(clean_dataset_doc)

    dashboard = frappe.new_doc("BI Dashboard")
    dashboard.dashboard_name = (
        spec.get("title")
        or clean_dataset_doc.dataset_title
        or f"Tableau de bord {clean_dataset_doc.name}"
    )
    # Avoid unique-name collisions
    base_name = dashboard.dashboard_name
    suffix = 1
    while frappe.db.exists("BI Dashboard", {"dashboard_name": dashboard.dashboard_name}):
        suffix += 1
        dashboard.dashboard_name = f"{base_name} ({suffix})"
    dashboard.dashboard_title = spec.get("title") or base_name
    dashboard.description = ""
    dashboard.dashboard_type = "AI Generated"
    dashboard.status = "Published"
    dashboard.dataset = frappe.db.get_value(
        "BI Dataset",
        {"source_import": clean_dataset_doc.source_import},
        "name",
    )
    dashboard.source_import = clean_dataset_doc.source_import
    dashboard.clean_dataset = clean_dataset_doc.name
    dashboard.ai_spec = spec_doc.name
    dashboard.quality_score = float(clean_dataset_doc.quality_score or 0)
    dashboard.is_system_suggested = 0
    dashboard.is_user_created = 0
    dashboard.generated_after_import = 1
    dashboard.created_at = now_datetime()
    dashboard.layout_json = json.dumps(spec.get("layout") or {}, ensure_ascii=False)
    dashboard.filters_json = json.dumps(
        [widget for widget in (spec.get("widgets") or []) if widget.get("type") == "filter"],
        ensure_ascii=False,
    )
    dashboard.widgets_json = json.dumps(spec.get("widgets") or [], ensure_ascii=False, default=str)
    dashboard.ai_summary = ""
    dashboard.insert(ignore_permissions=True)
    frappe.db.commit()

    for widget in spec.get("widgets") or []:
        if not isinstance(widget, dict):
            continue
        widget_type = widget.get("type")
        if widget_type not in ALLOWED_WIDGET_TYPES:
            raise ValueError(f"Type de widget dashboard.v1 non supporté: {widget_type}")

        widget_id = widget["id"]
        try:
            if widget_type == "kpi_card":
                data = calculate_kpi(df, widget)
            elif widget_type in {"bar_chart", "line_chart", "pie_chart"}:
                data = calculate_chart_data(df, widget)
            elif widget_type == "data_table":
                data = calculate_table_data(df, widget)
            elif widget_type == "filter":
                data = calculate_filter_data(df, widget)
            else:
                data = {"error": f"Type de widget non supporté: {widget_type}"}
        except Exception as exc:
            data = {"error": str(exc), "traceback": traceback.format_exc()[:500]}

        _make_widget(
            dashboard.name,
            widget_id,
            widget_type,
            widget.get("title") or widget_id,
            "",
            widget,
            data,
            widget.get("position"),
        )

    frappe.db.commit()
    return dashboard


# ----------------------------------------------------------------------------
# Read API for the frontend
# ----------------------------------------------------------------------------

@frappe.whitelist()
def get_intelligent_dashboard(dashboard_name: str) -> dict[str, Any]:
    """Return the dashboard + all widget rows ready for rendering."""
    dashboard = frappe.get_doc("BI Dashboard", dashboard_name)
    widgets = frappe.get_all(
        "BI Widget",
        filters={"dashboard": dashboard_name},
        fields=[
            "name", "widget_id", "widget_type", "title", "description",
            "config_json", "data_json", "position_json", "compute_status",
            "compute_error",
        ],
        order_by="creation asc",
        limit=200,
    )
    for widget in widgets:
        try:
            widget["config"] = json.loads(widget.pop("config_json") or "{}")
        except Exception:
            widget["config"] = {}
        widget["widget_type"] = _public_widget_type(widget.get("widget_type"), widget["config"])
        try:
            widget["data"] = json.loads(widget.pop("data_json") or "{}")
        except Exception:
            widget["data"] = {}
        try:
            widget["position"] = json.loads(widget.pop("position_json") or "{}")
        except Exception:
            widget["position"] = {}

    return {
        "name": dashboard.name,
        "dashboard_title": dashboard.dashboard_title or dashboard.dashboard_name,
        "description": dashboard.description,
        "ai_summary": dashboard.ai_summary,
        "quality_score": float(dashboard.quality_score or 0),
        "filters": json.loads(dashboard.filters_json or "[]"),
        "layout": json.loads(dashboard.layout_json or "[]"),
        "widgets": widgets,
        "clean_dataset": dashboard.clean_dataset,
        "ai_spec": dashboard.ai_spec,
    }


@frappe.whitelist()
def get_ai_spec_json(spec_name: str) -> dict[str, Any]:
    """Return the AI-generated JSON for the 'Voir le JSON généré' button."""
    doc = frappe.get_doc("BI AI Dashboard Spec", spec_name)
    try:
        validated = json.loads(doc.validated_json or "{}")
    except Exception:
        validated = {}
    try:
        response = json.loads(doc.response_json or "{}")
    except Exception:
        response = {}
    return {
        "name": doc.name,
        "validation_status": doc.validation_status,
        "validation_errors": doc.validation_errors,
        "validated_json": validated,
        "response_json": response,
        "model": doc.model,
    }
