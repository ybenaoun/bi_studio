import traceback
from types import SimpleNamespace

import frappe
import pandas as pd
from frappe import _
from frappe.utils import now_datetime

from bi_studio.api.importer import ensure_dataset_import_access, import_payload, read_import_dataframe
from bi_studio.api.profiling import append_log, create_job_log, update_job_log
from bi_studio.services.serialization import from_json, to_json
from bi_studio.services.warehouse import create_analytical_tables
from bi_studio.utils.data_types import (
    clean_series,
    normalize_category_value,
    normalize_dataframe_columns,
    parse_boolean_series,
    parse_date_series,
    parse_number_series,
)
from bi_studio.utils.quality import calculate_quality_score, dataframe_to_json_rows


ROLE_OPTIONS = {"Measure", "Dimension", "Date Dimension", "Identifier", "Attribute", "Ignored"}
TYPE_OPTIONS = {"Text", "Number", "Currency", "Date", "Boolean", "Category", "Identifier", "Unknown"}
AGGREGATIONS = {"sum", "average", "min", "max", "count", "count_distinct", "range", None, ""}


@frappe.whitelist()
def save_semantic_mapping(dataset_import_name, mapping_json):
    dataset_import = ensure_dataset_import_access(dataset_import_name, write=True)
    mapping = validate_mapping(dataset_import.name, from_json(mapping_json, mapping_json))
    existing = frappe.db.get_value("BI Semantic Mapping", {"dataset_import": dataset_import.name}, "name")
    payload = {
        "mapping_name": _("Mapping validé - {0}").format(dataset_import.dataset_name or dataset_import.name),
        "mapping_json": to_json(mapping),
        "validated_by": frappe.session.user,
        "validated_on": now_datetime(),
    }
    if existing:
        doc = frappe.get_doc("BI Semantic Mapping", existing)
        doc.update(payload)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": "BI Semantic Mapping", "dataset_import": dataset_import.name, **payload})
        doc.insert(ignore_permissions=True)

    dataset_import.import_log = append_log(dataset_import.import_log, _("Mapping sémantique validé."))
    dataset_import.save(ignore_permissions=True)
    return {"mapping": doc.name, "dataset_import": dataset_import.name}


@frappe.whitelist()
def start_transform(dataset_import_name):
    dataset_import = ensure_dataset_import_access(dataset_import_name, write=True)
    require_validated_mapping(dataset_import.name)
    dataset_import.status = "Transforming"
    dataset_import.error_message = None
    dataset_import.save(ignore_permissions=True)
    create_job_log(dataset_import.name, "Transform", "Started", _("Transformation mise en file d'attente."))
    frappe.enqueue(
        "bi_studio.api.etl.transform_dataset_job",
        queue="long",
        timeout=1200,
        enqueue_after_commit=True,
        dataset_import_name=dataset_import.name,
    )
    return {"queued": True, "dataset_import": dataset_import.name}


@frappe.whitelist()
def transform_dataset_job(dataset_import_name):
    dataset_import = frappe.get_doc("BI Dataset Import", dataset_import_name)
    log = create_job_log(dataset_import.name, "Transform", "Started", _("Transformation démarrée."))
    dataset = None
    try:
        file_doc, raw_df = read_import_dataframe(dataset_import)
        normalized_df, original_columns = normalize_dataframe_columns(raw_df)
        mapping_doc = require_validated_mapping(dataset_import.name)
        mapping = from_json(mapping_doc.mapping_json, {})
        mapped_columns = mapping.get("columns") or []
        normalized_df, rule_warnings = apply_transform_rules(dataset_import.name, normalized_df)
        clean_df, raw_for_tables, column_profiles, conversion_errors, unusable_columns = build_clean_dataframe(
            normalized_df, mapped_columns
        )
        quality = calculate_quality_score(clean_df, conversion_errors, unusable_columns)
        dataset_title = unique_dataset_name(dataset_import.dataset_name or "Dataset")

        dataset = frappe.get_doc(
            {
                "doctype": "BI Dataset",
                "dataset_name": dataset_title,
                "description": _("Dataset transformé depuis {0}").format(dataset_import.name),
                "status": "Processing",
                "source_file": dataset_import.import_file,
                "source_import": dataset_import.name,
                "row_count": len(clean_df.index),
                "column_count": len(clean_df.columns),
                "quality_score": quality["score"],
                "imported_at": now_datetime(),
                "last_transformed_on": now_datetime(),
                "clean_data_json": to_json(dataframe_to_json_rows(clean_df)),
                "schema_json": to_json(
                    {
                        "source_import": dataset_import.name,
                        "mapping": mapping,
                        "columns": [profile_to_schema(profile) for profile in column_profiles],
                        "quality": quality,
                        "rule_warnings": rule_warnings,
                    }
                ),
            }
        )
        append_dataset_columns(dataset, column_profiles)
        dataset.insert(ignore_permissions=True)

        create_analytical_tables(dataset, raw_for_tables, clean_df, [profile.compatible_profile for profile in column_profiles])
        dataset.status = "Ready"
        dataset.save(ignore_permissions=True)

        dataset_import.status = "Ready"
        dataset_import.import_log = append_log(
            dataset_import.import_log,
            _("Transformation terminée. Dataset {0}, qualité {1}/100.").format(dataset.name, quality["score"]),
        )
        dataset_import.save(ignore_permissions=True)

        update_job_log(log, "Success", _("Transformation terminée."))
        return {"dataset": dataset.name, "quality_score": quality["score"], "dataset_import": dataset_import.name}
    except Exception as exc:
        dataset_import.status = "Failed"
        dataset_import.error_message = str(exc)[:1000]
        dataset_import.import_log = append_log(dataset_import.import_log, _("Erreur transformation: {0}").format(str(exc)))
        dataset_import.save(ignore_permissions=True)
        if dataset:
            dataset.status = "Failed"
            dataset.save(ignore_permissions=True)
        update_job_log(log, "Failed", str(exc), traceback.format_exc())
        frappe.log_error(traceback.format_exc(), "BI Studio transform failed")
        raise


def validate_mapping(dataset_import_name, mapping):
    if isinstance(mapping, str):
        mapping = from_json(mapping, {})
    columns = mapping.get("columns") if isinstance(mapping, dict) else None
    if not columns:
        frappe.throw(_("Le mapping doit contenir une liste columns."))

    profiles = {
        row.normalized_column_name: row
        for row in frappe.get_all(
            "BI Column Profile",
            filters={"dataset_import": dataset_import_name},
            fields=["original_column_name", "normalized_column_name", "detected_type", "semantic_role"],
        )
    }
    if not profiles:
        frappe.throw(_("Lancez le profiling avant de valider le mapping."))

    cleaned_columns = []
    seen = set()
    for column in columns:
        normalized = column.get("normalized_name") or column.get("normalized_column_name")
        if normalized not in profiles:
            frappe.throw(_("Colonne inconnue dans le mapping: {0}").format(normalized))
        if normalized in seen:
            frappe.throw(_("Colonne dupliquée dans le mapping: {0}").format(normalized))
        seen.add(normalized)
        role = column.get("semantic_role")
        detected_type = column.get("detected_type") or profiles[normalized].detected_type
        aggregation = column.get("aggregation")
        if role not in ROLE_OPTIONS:
            frappe.throw(_("Rôle sémantique invalide pour {0}.").format(normalized))
        if detected_type not in TYPE_OPTIONS:
            frappe.throw(_("Type détecté invalide pour {0}.").format(normalized))
        if aggregation not in AGGREGATIONS:
            frappe.throw(_("Agrégation invalide pour {0}.").format(normalized))
        if role == "Measure" and detected_type not in {"Number", "Currency"}:
            frappe.throw(_("Une mesure doit être numérique: {0}.").format(normalized))
        if role == "Date Dimension" and detected_type != "Date":
            frappe.throw(_("Une dimension date doit être de type Date: {0}.").format(normalized))
        if role == "Identifier" and aggregation == "sum":
            frappe.throw(_("Un identifiant ne peut pas être additionné: {0}.").format(normalized))
        cleaned_columns.append(
            {
                "original_name": column.get("original_name") or profiles[normalized].original_column_name,
                "normalized_name": normalized,
                "detected_type": detected_type,
                "semantic_role": role,
                "aggregation": aggregation,
                "include": bool(column.get("include", role != "Ignored")),
            }
        )

    for normalized, profile in profiles.items():
        if normalized not in seen:
            cleaned_columns.append(
                {
                    "original_name": profile.original_column_name,
                    "normalized_name": normalized,
                    "detected_type": profile.detected_type,
                    "semantic_role": "Ignored",
                    "aggregation": None,
                    "include": False,
                }
            )
    return {"columns": cleaned_columns}


def require_validated_mapping(dataset_import_name):
    mapping_name = frappe.db.get_value(
        "BI Semantic Mapping",
        {"dataset_import": dataset_import_name, "validated_by": ["is", "set"]},
        "name",
    )
    if not mapping_name:
        frappe.throw(_("Validez le mapping sémantique avant de transformer le dataset."))
    return frappe.get_doc("BI Semantic Mapping", mapping_name)


def apply_transform_rules(dataset_import_name, df):
    warnings = []
    for rule in frappe.get_all(
        "BI Transform Rule",
        filters={"dataset_import": dataset_import_name, "enabled": 1},
        fields=["column_name", "rule_type", "rule_config_json"],
        order_by="creation asc",
    ):
        column = rule.column_name
        config = from_json(rule.rule_config_json, {})
        if rule.rule_type != "Remove Duplicates" and column not in df.columns:
            warnings.append(f"Rule skipped, missing column: {column}")
            continue
        if rule.rule_type == "Trim Text":
            df[column] = clean_series(df[column]).map(lambda value: value.strip() if isinstance(value, str) else value)
        elif rule.rule_type == "Normalize Category":
            df[column] = df[column].map(normalize_category_value)
        elif rule.rule_type == "Convert Date":
            df[column] = parse_date_series(df[column]).dt.date
        elif rule.rule_type == "Convert Number":
            df[column] = parse_number_series(df[column])
        elif rule.rule_type == "Fill Missing":
            df[column] = df[column].where(pd.notnull(df[column]), config.get("value"))
        elif rule.rule_type == "Drop Column":
            df = df.drop(columns=[column])
        elif rule.rule_type == "Rename Column":
            new_name = config.get("new_name")
            if new_name:
                df = df.rename(columns={column: new_name})
        elif rule.rule_type == "Remove Duplicates":
            df = df.drop_duplicates().reset_index(drop=True)
        elif rule.rule_type == "Custom Formula":
            warnings.append("Custom Formula ignored: arbitrary formulas are disabled for security.")
    return df, warnings


def build_clean_dataframe(df, mapped_columns):
    clean_columns = {}
    raw_columns = {}
    profiles = []
    conversion_errors = {}
    unusable_columns = 0
    row_count = len(df.index)

    for column in mapped_columns:
        normalized = column.get("normalized_name")
        if not column.get("include", True) or column.get("semantic_role") == "Ignored":
            unusable_columns += 1
            continue
        if normalized not in df.columns:
            unusable_columns += 1
            conversion_errors[normalized] = row_count
            continue

        detected_type = column.get("detected_type")
        role = column.get("semantic_role")
        raw_series = clean_series(df[normalized])
        converted, errors = convert_series(raw_series, detected_type, role)
        clean_columns[normalized] = converted
        raw_columns[normalized] = raw_series
        conversion_errors[normalized] = errors
        profiles.append(make_dataset_profile(column, converted, errors))

    if not clean_columns:
        frappe.throw(_("Aucune colonne exploitable après mapping."))
    clean_df = pd.DataFrame(clean_columns)
    raw_for_tables = pd.DataFrame(raw_columns)
    return clean_df, raw_for_tables, profiles, conversion_errors, unusable_columns


def convert_series(series, detected_type, role):
    non_null_count = int(series.notna().sum())
    if role == "Measure":
        converted = parse_number_series(series)
        errors = int((series.notna() & converted.isna()).sum())
        return converted, errors
    if role == "Date Dimension":
        dates = parse_date_series(series)
        errors = int((series.notna() & dates.isna()).sum())
        return dates.dt.date.where(pd.notnull(dates), None), errors
    if detected_type == "Boolean":
        booleans = parse_boolean_series(series)
        errors = int((series.notna() & booleans.isna()).sum())
        return booleans.map(lambda value: "Yes" if value is True else "No" if value is False else None), errors
    if role == "Dimension":
        return series.map(normalize_category_value), 0
    converted = series.map(lambda value: str(value).strip() if value is not None else None)
    return converted, 0 if non_null_count else 0


def make_dataset_profile(column, series, conversion_errors):
    role = column.get("semantic_role")
    detected_type = column.get("detected_type")
    data_type = "Number" if role == "Measure" else "Date" if role == "Date Dimension" else "Text"
    null_count = int(series.isna().sum())
    distinct_count = int(series.nunique(dropna=True))
    profile = SimpleNamespace(
        label=column.get("original_name") or column.get("normalized_name"),
        name=column.get("normalized_name"),
        data_type=data_type,
        detected_type=detected_type,
        semantic_role=role,
        aggregation=column.get("aggregation"),
        include=bool(column.get("include", True)),
        is_numeric=role == "Measure",
        is_category=role in {"Dimension", "Date Dimension"},
        is_date=role == "Date Dimension",
        distinct_count=distinct_count,
        null_count=null_count,
        null_rate=round(null_count / len(series.index), 4) if len(series.index) else 0,
        sample_values=[str(value) for value in series.dropna().head(5).tolist()],
        conversion_errors=conversion_errors,
    )
    profile.compatible_profile = SimpleNamespace(
        label=profile.label,
        name=profile.name,
        data_type=profile.data_type,
        is_numeric=profile.is_numeric,
        is_category=profile.is_category,
        is_date=profile.is_date,
        distinct_count=profile.distinct_count,
        null_count=profile.null_count,
        sample_values=profile.sample_values,
    )
    return profile


def append_dataset_columns(dataset, profiles):
    dataset.set("columns", [])
    for index, profile in enumerate(profiles, start=1):
        dataset.append(
            "columns",
            {
                "column_label": profile.label,
                "column_name": profile.name,
                "data_type": profile.data_type,
                "detected_type": profile.detected_type,
                "semantic_role": profile.semantic_role,
                "aggregation": profile.aggregation,
                "include_in_analysis": int(profile.include),
                "display_order": index,
                "is_numeric": int(profile.is_numeric),
                "is_category": int(profile.is_category),
                "is_date": int(profile.is_date),
                "distinct_count": profile.distinct_count,
                "null_count": profile.null_count,
                "null_rate": profile.null_rate,
                "sample_values_json": to_json(profile.sample_values),
                "warnings": to_json({"conversion_errors": profile.conversion_errors}),
            },
        )


def profile_to_schema(profile):
    return {
        "label": profile.label,
        "name": profile.name,
        "data_type": profile.data_type,
        "detected_type": profile.detected_type,
        "semantic_role": profile.semantic_role,
        "aggregation": profile.aggregation,
        "include": profile.include,
        "distinct_count": profile.distinct_count,
        "null_count": profile.null_count,
        "null_rate": profile.null_rate,
    }


def unique_dataset_name(base):
    base = (base or "Dataset").strip()[:120]
    name = base
    counter = 2
    while frappe.db.exists("BI Dataset", {"dataset_name": name}):
        name = f"{base} {counter}"
        counter += 1
    return name
