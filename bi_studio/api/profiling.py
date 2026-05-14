import traceback

import frappe
from frappe import _
from frappe.utils import now_datetime

from bi_studio.api.importer import ensure_dataset_import_access, import_payload, read_import_dataframe
from bi_studio.services.serialization import to_json
from bi_studio.utils.data_types import mapping_from_profiles, profile_dataframe


@frappe.whitelist()
def start_profiling(dataset_import_name):
    dataset_import = ensure_dataset_import_access(dataset_import_name, write=True)
    dataset_import.status = "Profiling"
    dataset_import.error_message = None
    dataset_import.save(ignore_permissions=True)
    create_job_log(dataset_import.name, "Profiling", "Started", _("Profiling mis en file d'attente."))
    frappe.enqueue(
        "bi_studio.api.profiling.profile_dataset_job",
        queue="long",
        timeout=900,
        enqueue_after_commit=True,
        dataset_import_name=dataset_import.name,
    )
    return {"queued": True, "dataset_import": dataset_import.name}


@frappe.whitelist()
def profile_dataset_job(dataset_import_name):
    dataset_import = frappe.get_doc("BI Dataset Import", dataset_import_name)
    log = create_job_log(dataset_import.name, "Profiling", "Started", _("Profiling démarré."))
    try:
        file_doc, raw_df = read_import_dataframe(dataset_import)
        normalized_df, profiles = profile_dataframe(raw_df)

        for name in frappe.get_all("BI Column Profile", filters={"dataset_import": dataset_import.name}, pluck="name"):
            frappe.delete_doc("BI Column Profile", name, ignore_permissions=True, force=True)

        for profile in profiles:
            frappe.get_doc(
                {
                    "doctype": "BI Column Profile",
                    "dataset_import": dataset_import.name,
                    "original_column_name": profile.original_column_name,
                    "normalized_column_name": profile.normalized_column_name,
                    "detected_type": profile.detected_type,
                    "semantic_role": profile.semantic_role,
                    "null_count": profile.null_count,
                    "null_rate": profile.null_rate,
                    "unique_count": profile.unique_count,
                    "sample_values": to_json(profile.sample_values),
                    "min_value": profile.min_value,
                    "max_value": profile.max_value,
                    "mean_value": profile.mean_value,
                    "confidence": profile.confidence,
                    "warnings": to_json(profile.warnings or []),
                }
            ).insert(ignore_permissions=True)

        dataset_import.status = "Mapping Required"
        dataset_import.row_count = len(normalized_df.index)
        dataset_import.column_count = len(normalized_df.columns)
        dataset_import.import_log = append_log(dataset_import.import_log, _("Profiling terminé: {0} colonnes.").format(len(profiles)))
        dataset_import.save(ignore_permissions=True)

        update_job_log(log, "Success", _("Profiling terminé."))
        ensure_initial_mapping(dataset_import, profiles)
        return import_payload(dataset_import, include_profiles=True)
    except Exception as exc:
        dataset_import.status = "Failed"
        dataset_import.error_message = str(exc)[:1000]
        dataset_import.import_log = append_log(dataset_import.import_log, _("Erreur profiling: {0}").format(str(exc)))
        dataset_import.save(ignore_permissions=True)
        update_job_log(log, "Failed", str(exc), traceback.format_exc())
        frappe.log_error(traceback.format_exc(), "BI Studio profiling failed")
        raise


def ensure_initial_mapping(dataset_import, profiles):
    if frappe.db.exists("BI Semantic Mapping", {"dataset_import": dataset_import.name}):
        return
    frappe.get_doc(
        {
            "doctype": "BI Semantic Mapping",
            "dataset_import": dataset_import.name,
            "mapping_name": _("Mapping proposé - {0}").format(dataset_import.dataset_name or dataset_import.name),
            "mapping_json": to_json(mapping_from_profiles(profiles)),
            "is_template": 0,
            "validated_by": None,
            "validated_on": None,
        }
    ).insert(ignore_permissions=True)


def create_job_log(dataset_import_name, job_type, status, message=None):
    log = frappe.get_doc(
        {
            "doctype": "BI ETL Job Log",
            "dataset_import": dataset_import_name,
            "job_type": job_type,
            "status": status,
            "started_on": now_datetime(),
            "log_message": message,
        }
    )
    log.insert(ignore_permissions=True)
    return log.name


def update_job_log(log_name, status, message=None, error_traceback=None):
    if not log_name:
        return
    doc = frappe.get_doc("BI ETL Job Log", log_name)
    doc.status = status
    doc.finished_on = now_datetime()
    doc.log_message = message
    doc.error_traceback = error_traceback
    doc.save(ignore_permissions=True)


def append_log(current, message):
    prefix = current or ""
    line = f"[{now_datetime()}] {message}"
    return f"{prefix}\n{line}".strip()
