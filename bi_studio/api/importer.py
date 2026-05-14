import os

import frappe
import pandas as pd
from frappe import _
from frappe.utils import cint, now_datetime

from bi_studio.config import MAX_IMPORT_ROWS
from bi_studio.services.etl import resolve_file
from bi_studio.services.permissions import ensure_authenticated, is_admin
from bi_studio.services.serialization import from_json, to_json
from bi_studio.utils.data_types import dataframe_preview, normalize_dataframe_columns


PREVIEW_LIMIT = 50
MAX_IMPORT_FILE_SIZE_MB = 10


def ensure_dataset_import_access(dataset_import_name, write=False):
    ensure_authenticated()
    doc = frappe.get_doc("BI Dataset Import", dataset_import_name)
    if is_admin() or doc.owner == frappe.session.user or doc.owner_user == frappe.session.user:
        return doc
    frappe.throw(_("Vous n'avez pas accès à cet import BI."), frappe.PermissionError)


def validate_file_size(file_doc):
    size = cint(getattr(file_doc, "file_size", 0) or 0)
    if size and size > MAX_IMPORT_FILE_SIZE_MB * 1024 * 1024:
        frappe.throw(_(f"Le fichier dépasse la limite de {MAX_IMPORT_FILE_SIZE_MB} Mo."))


def get_excel_file(file_url=None, file_name=None):
    file_doc, path = resolve_file(file_url=file_url, file_name=file_name)
    validate_file_size(file_doc)
    if not os.path.exists(path):
        frappe.throw(_("Le fichier Excel est introuvable sur le serveur."))
    return file_doc, path


def get_workbook(path):
    try:
        return pd.ExcelFile(path)
    except Exception as exc:
        frappe.throw(_("Impossible de lire le fichier Excel: {0}").format(str(exc)))


def detect_header_row(path, sheet_name):
    preview = pd.read_excel(path, sheet_name=sheet_name, header=None, nrows=25, dtype=object)
    best_index = 0
    best_score = -1
    for index, row in preview.iterrows():
        values = [str(value).strip() for value in row.tolist() if value is not None and not pd.isna(value)]
        if not values:
            continue
        unique_values = len(set(value.lower() for value in values))
        textish = sum(1 for value in values if any(char.isalpha() for char in value))
        next_non_empty = 0
        if index + 1 < len(preview.index):
            next_non_empty = int(preview.iloc[index + 1].notna().sum())
        score = unique_values * 3 + textish * 2 + min(next_non_empty, unique_values)
        if score > best_score:
            best_index = int(index)
            best_score = score
    return best_index + 1


def normalize_header_row(header_row):
    if header_row in (None, ""):
        return None
    value = max(1, cint(header_row))
    return value


def read_import_dataframe(dataset_import):
    file_doc, path = get_excel_file(file_url=dataset_import.import_file)
    sheet_name = dataset_import.selected_sheet
    header_row = normalize_header_row(dataset_import.header_row) or detect_header_row(path, sheet_name)
    try:
        df = pd.read_excel(path, sheet_name=sheet_name, header=header_row - 1, dtype=object)
    except Exception as exc:
        frappe.throw(_("Lecture Excel impossible: {0}").format(str(exc)))
    if len(df.index) > MAX_IMPORT_ROWS:
        frappe.throw(_(f"Le fichier dépasse la limite autorisée de {MAX_IMPORT_ROWS} lignes."))
    if len(df.columns) == 0:
        frappe.throw(_("Le fichier ne contient aucune colonne exploitable."))
    return file_doc, df


@frappe.whitelist()
def upload_and_preview_excel(file_url, sheet_name=None, header_row=None):
    ensure_authenticated()
    file_doc, path = get_excel_file(file_url=file_url)
    workbook = get_workbook(path)
    sheets = workbook.sheet_names
    if not sheets:
        frappe.throw(_("Le fichier Excel ne contient aucune feuille."))
    selected_sheet = sheet_name if sheet_name in sheets else sheets[0]
    selected_header_row = normalize_header_row(header_row) or detect_header_row(path, selected_sheet)

    try:
        df = pd.read_excel(path, sheet_name=selected_sheet, header=selected_header_row - 1, dtype=object)
    except Exception as exc:
        frappe.throw(_("Lecture Excel impossible: {0}").format(str(exc)))

    if len(df.index) > MAX_IMPORT_ROWS:
        frappe.throw(_(f"Le fichier dépasse la limite autorisée de {MAX_IMPORT_ROWS} lignes."))

    normalized_df, original_labels = normalize_dataframe_columns(df)
    preview = dataframe_preview(normalized_df, PREVIEW_LIMIT)
    dataset_name = (file_doc.file_name or "Dataset").rsplit(".", 1)[0]

    dataset_import = frappe.get_doc(
        {
            "doctype": "BI Dataset Import",
            "import_file": file_doc.file_url,
            "dataset_name": dataset_name,
            "selected_sheet": selected_sheet,
            "header_row": selected_header_row,
            "status": "Uploaded",
            "row_count": len(normalized_df.index),
            "column_count": len(normalized_df.columns),
            "detected_sheets": to_json(sheets),
            "preview_data": to_json(
                {
                    "columns": [
                        {"label": original_labels[index] if index < len(original_labels) else column, "name": column}
                        for index, column in enumerate(normalized_df.columns)
                    ],
                    "rows": preview,
                }
            ),
            "import_log": _("Fichier téléversé et prévisualisé le {0}.").format(now_datetime()),
            "owner_user": frappe.session.user,
        }
    )
    dataset_import.insert(ignore_permissions=True)

    return import_payload(dataset_import)


@frappe.whitelist()
def get_dataset_import(dataset_import_name):
    dataset_import = ensure_dataset_import_access(dataset_import_name)
    return import_payload(dataset_import, include_profiles=True)


@frappe.whitelist()
def update_import_preview(dataset_import_name, sheet_name=None, header_row=None):
    dataset_import = ensure_dataset_import_access(dataset_import_name, write=True)
    file_doc, path = get_excel_file(file_url=dataset_import.import_file)
    workbook = get_workbook(path)
    sheets = workbook.sheet_names
    selected_sheet = sheet_name if sheet_name in sheets else dataset_import.selected_sheet or sheets[0]
    selected_header_row = normalize_header_row(header_row) or detect_header_row(path, selected_sheet)
    df = pd.read_excel(path, sheet_name=selected_sheet, header=selected_header_row - 1, dtype=object)
    if len(df.index) > MAX_IMPORT_ROWS:
        frappe.throw(_(f"Le fichier dépasse la limite autorisée de {MAX_IMPORT_ROWS} lignes."))
    normalized_df, original_labels = normalize_dataframe_columns(df)
    dataset_import.selected_sheet = selected_sheet
    dataset_import.header_row = selected_header_row
    dataset_import.row_count = len(normalized_df.index)
    dataset_import.column_count = len(normalized_df.columns)
    dataset_import.detected_sheets = to_json(sheets)
    dataset_import.preview_data = to_json(
        {
            "columns": [
                {"label": original_labels[index] if index < len(original_labels) else column, "name": column}
                for index, column in enumerate(normalized_df.columns)
            ],
            "rows": dataframe_preview(normalized_df, PREVIEW_LIMIT),
        }
    )
    dataset_import.import_log = (dataset_import.import_log or "") + f"\nPreview mise à jour: {selected_sheet}, header {selected_header_row}."
    dataset_import.save(ignore_permissions=True)
    return import_payload(dataset_import, include_profiles=True)


@frappe.whitelist()
def list_dataset_imports(limit=20):
    ensure_authenticated()
    filters = {} if is_admin() else {"owner": frappe.session.user}
    rows = frappe.get_list(
        "BI Dataset Import",
        filters=filters,
        fields=[
            "name",
            "dataset_name",
            "status",
            "row_count",
            "column_count",
            "selected_sheet",
            "created_dashboard",
            "modified",
        ],
        order_by="modified desc",
        limit_page_length=cint(limit) or 20,
    )
    return rows


def import_payload(dataset_import, include_profiles=False):
    payload = {
        "import": dataset_import.as_dict(),
        "detected_sheets": from_json(dataset_import.detected_sheets, []),
        "preview": from_json(dataset_import.preview_data, {"columns": [], "rows": []}),
    }
    if not include_profiles:
        return payload

    profiles = frappe.get_all(
        "BI Column Profile",
        filters={"dataset_import": dataset_import.name},
        fields=[
            "name",
            "original_column_name",
            "normalized_column_name",
            "detected_type",
            "semantic_role",
            "null_count",
            "null_rate",
            "unique_count",
            "sample_values",
            "min_value",
            "max_value",
            "mean_value",
            "confidence",
            "warnings",
        ],
        order_by="creation asc",
    )
    for profile in profiles:
        profile["sample_values"] = from_json(profile.sample_values, [])
        profile["warnings"] = from_json(profile.warnings, [])

    mappings = frappe.get_all(
        "BI Semantic Mapping",
        filters={"dataset_import": dataset_import.name},
        fields=["name", "mapping_name", "mapping_json", "validated_by", "validated_on"],
        order_by="modified desc",
        limit=1,
    )
    mapping = mappings[0] if mappings else None
    if mapping:
        mapping["mapping_json"] = from_json(mapping.mapping_json, {})

    payload.update(
        {
            "profiles": profiles,
            "mapping": mapping,
            "dataset": frappe.db.get_value("BI Dataset", {"source_import": dataset_import.name}, "name"),
            "dashboard": dataset_import.created_dashboard,
        }
    )
    return payload
