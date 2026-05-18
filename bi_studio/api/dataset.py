"""API REST pour les datasets BI Studio."""

from __future__ import annotations

import frappe
from frappe import _

from bi_studio.services.cleanup import delete_dataset_cascade
from bi_studio.services.export import export_clean_dataset_file
from bi_studio.services.permissions import (
	ensure_authenticated,
	ensure_dataset_access,
	is_admin,
)
from bi_studio.services.query import get_clean_rows, get_dataset_columns
from bi_studio.services.serialization import from_json


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


def _dataset_list_row(row: dict) -> dict:
	row["is_favorite"] = _is_favorite("BI Dataset", row["name"])
	return row


@frappe.whitelist()
def get_datasets(search: str | None = None, status: str | None = None, limit: int = 100):
	ensure_authenticated()
	filters: dict = {}
	if status:
		filters["status"] = status
	or_filters = {}
	if search:
		or_filters = {
			"dataset_name": ["like", f"%{search}%"],
			"description": ["like", f"%{search}%"],
			"name": ["like", f"%{search}%"],
		}
	rows = frappe.get_list(
		"BI Dataset",
		filters=filters,
		or_filters=or_filters,
		fields=[
			"name",
			"dataset_name",
			"description",
			"tags",
			"status",
			"row_count",
			"column_count",
			"quality_score",
			"imported_at",
			"last_transformed_on",
			"suggested_dashboard",
			"source_import",
			"import_job",
			"modified",
			"owner",
		],
		order_by="modified desc",
		limit_page_length=int(limit or 100),
	)
	return [_dataset_list_row(row) for row in rows]


# Alias rétrocompatibilité
@frappe.whitelist()
def list_datasets(search: str | None = None, status: str | None = None, limit: int = 100):
	return get_datasets(search=search, status=status, limit=limit)


def _auto_kpis(dataset_doc, columns: list) -> list:
	"""Renvoie un KPI rapide par colonne mesure (au plus 4)."""
	from bi_studio.services.query import get_kpi_value

	kpis = [{"label": _("Nombre de lignes"), "value": int(dataset_doc.row_count or 0)}]
	measures = [c for c in columns if c.get("semantic_role") == "Measure" or c.get("is_numeric")]
	for column in measures[:3]:
		try:
			value = get_kpi_value(
				dataset_doc,
				{"value_field": column["column_name"], "calculation_type": "Total"},
			)
			kpis.append({"label": _("Total {0}").format(column.get("column_label") or column["column_name"]), "value": value})
		except Exception:
			continue
	return kpis


def _auto_charts(dataset_doc, columns: list) -> list:
	"""Renvoie un graphique automatique mesure x dimension si possible."""
	from bi_studio.services.query import get_chart_data

	measures = [c for c in columns if c.get("semantic_role") == "Measure" or c.get("is_numeric")]
	dimensions = [
		c for c in columns
		if c.get("semantic_role") in {"Dimension", "Date Dimension"} or c.get("is_category") or c.get("is_date")
	]
	if not measures or not dimensions:
		return []
	charts = []
	pairs = list(zip(measures[:2], dimensions[:2]))
	for measure, dimension in pairs:
		try:
			data = get_chart_data(
				dataset_doc,
				{
					"chart_type": "Bar",
					"value_field": measure["column_name"],
					"category_field": dimension["column_name"],
					"calculation_type": "Total",
				},
			)
			charts.append(
				{
					"name": f"auto_{measure['column_name']}_{dimension['column_name']}",
					"title": _("{0} par {1}").format(
						measure.get("column_label") or measure["column_name"],
						dimension.get("column_label") or dimension["column_name"],
					),
					"chart_type": "Bar",
					"data": data,
				}
			)
		except Exception:
			continue
	return charts


@frappe.whitelist()
def get_dataset_detail(
	dataset_name: str,
	start: int = 0,
	page_length: int = 50,
	search: str | None = None,
	order_by: str | None = None,
	order_dir: str = "asc",
):
	"""Renvoie les métadonnées du dataset, ses colonnes et une page de lignes nettoyées."""
	dataset = ensure_dataset_access(dataset_name)
	columns = get_dataset_columns(dataset.name)
	clean_available = False
	try:
		clean = get_clean_rows(
			dataset,
			start=int(start or 0),
			page_length=int(page_length or 50),
			search=search,
			order_by=order_by,
			order_dir=order_dir,
		)
		rows = clean.get("rows") or []
		total = clean.get("total") or 0
		clean_available = True
	except Exception as error:
		rows = []
		total = 0
		frappe.log_error(f"get_clean_rows({dataset.name}) failed: {error}", "BI Studio")

	cleaned_data = {"columns": columns, "rows": rows, "total": total}
	kpis = _auto_kpis(dataset, columns) if clean_available else []
	charts = _auto_charts(dataset, columns) if clean_available else []

	return {
		"dataset": {
			"name": dataset.name,
			"dataset_name": dataset.dataset_name,
			"description": dataset.description,
			"tags": dataset.tags,
			"status": dataset.status,
			"row_count": dataset.row_count,
			"column_count": dataset.column_count,
			"quality_score": dataset.quality_score,
			"imported_at": dataset.imported_at,
			"last_transformed_on": dataset.last_transformed_on,
			"suggested_dashboard": dataset.suggested_dashboard,
			"source_file": dataset.source_file,
			"source_import": dataset.source_import,
			"import_job": dataset.import_job,
			"raw_table": dataset.raw_table,
			"clean_table": dataset.clean_table,
			"fact_table": dataset.fact_table,
			"warehouse_model": from_json(dataset.warehouse_model_json, {}),
			"owner": dataset.owner,
			"modified": dataset.modified,
		},
		"columns": columns,
		"rows": rows,
		"total": total,
		"cleaned_data": cleaned_data,
		"kpis": kpis,
		"charts": charts,
		"suggested_dashboard": dataset.suggested_dashboard,
		"is_favorite": _is_favorite("BI Dataset", dataset.name),
	}


@frappe.whitelist()
def get_dataset(dataset: str, **kwargs):
	"""Alias rétrocompatibilité."""
	return get_dataset_detail(dataset, **kwargs)


@frappe.whitelist()
def rename_dataset(dataset_name: str, new_name: str):
	dataset = ensure_dataset_access(dataset_name, ptype="write")
	clean = (new_name or "").strip()
	if not clean:
		frappe.throw(_("Nouveau nom requis."))
	dataset.dataset_name = clean
	dataset.save(ignore_permissions=is_admin())
	return {"dataset": dataset.name, "dataset_name": dataset.dataset_name}


@frappe.whitelist()
def delete_dataset(dataset_name: str):
	ensure_dataset_access(dataset_name, ptype="delete")
	delete_dataset_cascade(dataset_name)
	return {"deleted": True, "dataset_name": dataset_name}


@frappe.whitelist()
def export_clean_dataset(dataset_name: str):
	ensure_dataset_access(dataset_name)
	return export_clean_dataset_file(dataset_name)
