import frappe
from frappe import _

from wizio_erp.services.permissions import (
	assert_dataset_read,
	assert_dataset_write,
	can_read_dataset,
	require_authenticated,
)
from wizio_erp.utils.data import apply_filters, dataframe_payload, read_tabular_file, resolve_file_path, to_json


@frappe.whitelist()
def import_dataset(file_url: str, dataset_name: str | None = None):
	require_authenticated()
	path = resolve_file_path(file_url)
	df = read_tabular_file(path)
	payload = dataframe_payload(df)
	doc = frappe.new_doc("Wizio Dataset")
	doc.dataset_name = dataset_name or frappe.get_value("File", {"file_url": file_url}, "file_name") or "Dataset"
	doc.status = "Ready"
	doc.source_file = file_url
	doc.imported_by = frappe.session.user
	doc.row_count = len(payload["rows"])
	doc.column_count = len(payload["schema"])
	doc.data_json = to_json(payload["rows"])
	doc.schema_json = to_json(payload["schema"])
	doc.preview_json = to_json(payload["preview"])
	doc.insert(ignore_permissions=True)
	return {"dataset": doc.name, "dataset_name": doc.dataset_name, "row_count": doc.row_count}


@frappe.whitelist()
def create_dataset(dataset_name: str, rows_json: str = "[]", schema_json: str = "[]"):
	require_authenticated()
	rows = frappe.parse_json(rows_json) or []
	schema = frappe.parse_json(schema_json) or []
	if not isinstance(rows, list):
		frappe.throw(_("Les lignes du dataset doivent etre une liste JSON."))
	doc = frappe.new_doc("Wizio Dataset")
	doc.dataset_name = dataset_name
	doc.status = "Ready"
	doc.imported_by = frappe.session.user
	doc.row_count = len(rows)
	doc.column_count = len(schema) if schema else (len(rows[0]) if rows else 0)
	doc.data_json = to_json(rows)
	doc.schema_json = to_json(schema)
	doc.preview_json = to_json({"columns": schema, "rows": rows[:50]})
	doc.insert(ignore_permissions=True)
	return {"dataset": doc.name}


@frappe.whitelist()
def list_datasets(search: str | None = None, status: str | None = None, limit: int = 100):
	require_authenticated()
	filters = {}
	if status:
		filters["status"] = status
	or_filters = {}
	if search:
		or_filters = {"dataset_name": ["like", f"%{search}%"], "name": ["like", f"%{search}%"]}
	rows = frappe.get_list(
		"Wizio Dataset",
		filters=filters,
		or_filters=or_filters,
		fields=["name", "dataset_name", "status", "imported_by", "row_count", "column_count", "modified"],
		order_by="modified desc",
		limit_page_length=int(limit or 100),
	)
	return [row for row in rows if can_read_dataset(row.name)]


@frappe.whitelist()
def get_dataset(dataset: str, filters_json: str | None = None, start: int = 0, page_length: int = 50):
	doc = assert_dataset_read(dataset)
	rows = frappe.parse_json(doc.data_json or "[]") or []
	rows = apply_filters(rows, frappe.parse_json(filters_json or "{}") or {})
	start = int(start or 0)
	page_length = int(page_length or 50)
	return {
		"dataset": doc.as_dict(),
		"schema": frappe.parse_json(doc.schema_json or "[]") or [],
		"rows": rows[start : start + page_length],
		"total": len(rows),
	}


@frappe.whitelist()
def update_dataset(dataset: str, dataset_name: str | None = None, rows_json: str | None = None):
	doc = assert_dataset_write(dataset)
	if dataset_name is not None:
		doc.dataset_name = dataset_name.strip()
	if rows_json is not None:
		rows = frappe.parse_json(rows_json) or []
		if not isinstance(rows, list):
			frappe.throw(_("Le contenu du dataset doit etre une liste JSON."))
		doc.data_json = to_json(rows)
		doc.row_count = len(rows)
		doc.preview_json = to_json({"columns": frappe.parse_json(doc.schema_json or "[]") or [], "rows": rows[:50]})
	doc.save(ignore_permissions=True)
	return {"dataset": doc.name, "dataset_name": doc.dataset_name}


@frappe.whitelist()
def delete_dataset(dataset: str):
	doc = assert_dataset_write(dataset)
	frappe.delete_doc("Wizio Dataset", doc.name, ignore_permissions=True)
	return {"deleted": True}
