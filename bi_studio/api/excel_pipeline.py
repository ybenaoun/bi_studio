"""Pipeline: Excel raw -> ETL -> Profile -> userIntent -> Cohere -> Validated spec -> Dashboard.

Whitelisted entrypoints:
    run_excel_to_dashboard_pipeline(file_url, dataset_title, sheet_name, header_row)
        → Steps 1-5 (ETL). Sets status="ETL Complete" and returns.

    submit_user_intent_and_generate(import_name, user_intent_json)
        → Steps 6-8 (Cohere + Validate + Build). Enqueued after user chooses their
          analysis goals, KPIs and dimensions.

    get_pipeline_status(import_name)
        → Returns pipeline state, ETL logs, and column_metadata (when ETL complete).

    retry_ai_generation(import_name)
        → Re-runs steps 6-8 from an existing clean dataset.
"""
from __future__ import annotations

import json
import logging
import traceback
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import frappe
import pandas as pd
from frappe.utils import now_datetime

from bi_studio.api.column_renamer import rename_columns_to_readable_names
from bi_studio.api.dataset_profiler import profile_dataset, schema_from_profile
from bi_studio.api.etl_cleaning import clean_dataset
from bi_studio.services.etl import resolve_file
from bi_studio.services.warehouse import create_analytical_tables
from bi_studio.utils.data_quality import compute_dataset_quality
from bi_studio.utils.data_types import dataframe_preview


PIPELINE_QUEUE = "long"
MAX_PROMPT_PREVIEW_ROWS = 20

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ETL log helper
# ---------------------------------------------------------------------------

def log_step(
    import_name: str,
    step: str,
    status: str,
    message: str = "",
    traceback_text: str = "",
    started_on: datetime | None = None,
) -> None:
    """Create a BI ETL Job Log entry. Best-effort: never raise."""
    try:
        doc = frappe.new_doc("BI ETL Job Log")
        doc.dataset_import = import_name
        doc.job_type = step
        doc.status = status
        doc.started_on = started_on or now_datetime()
        if status in {"Success", "Failed"}:
            doc.finished_on = now_datetime()
        doc.log_message = (message or "")[:5000]
        doc.error_traceback = (traceback_text or "")[:5000]
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
    except Exception:
        frappe.log_error(
            title="BI Pipeline: log_step a échoué",
            message=traceback.format_exc(),
        )


# ---------------------------------------------------------------------------
# Excel extraction
# ---------------------------------------------------------------------------

def _detect_header_row(df_no_header: pd.DataFrame, max_scan: int = 25) -> int:
    """Pick the first row that has multiple non-empty text cells AND is followed
    by data. Returns 0-indexed header row.
    """
    best_row = 0
    best_score = -1
    rows_to_scan = min(max_scan, len(df_no_header.index))
    for i in range(rows_to_scan):
        row = df_no_header.iloc[i]
        non_empty = row.dropna()
        if non_empty.empty:
            continue
        text_cells = sum(1 for v in non_empty if isinstance(v, str) and v.strip())
        non_empty_count = len(non_empty)
        score = text_cells * 2 + non_empty_count
        if i + 1 < len(df_no_header.index):
            next_non_empty = df_no_header.iloc[i + 1].dropna().size
            score += min(next_non_empty, 10)
        if score > best_score:
            best_score = score
            best_row = i
    return best_row


def extract_excel(import_doc: Any) -> pd.DataFrame:
    """Read the Excel file, detect sheet + header row, return cleaned-of-empty df.

    Updates the import_doc with:
        selected_sheet, header_row, row_count_raw, column_count_raw,
        raw_preview_json, detected_sheets_json
    """
    _, file_path = resolve_file(file_url=import_doc.import_file)

    excel = pd.ExcelFile(file_path)
    detected_sheets = excel.sheet_names

    sheet_name = import_doc.selected_sheet or _pick_best_sheet(excel)
    if sheet_name not in detected_sheets:
        sheet_name = detected_sheets[0]

    raw = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
    if import_doc.header_row and int(import_doc.header_row) > 0:
        header_row = int(import_doc.header_row) - 1
    else:
        header_row = _detect_header_row(raw)

    df = pd.read_excel(file_path, sheet_name=sheet_name, header=header_row)
    df = df.dropna(how="all").reset_index(drop=True)
    df = df.dropna(axis=1, how="all")

    import_doc.selected_sheet = sheet_name
    import_doc.header_row = header_row + 1
    import_doc.row_count_raw = int(len(df.index))
    import_doc.column_count_raw = int(len(df.columns))
    import_doc.raw_preview_json = json.dumps(
        dataframe_preview(df, limit=50), default=str, ensure_ascii=False
    )
    import_doc.detected_sheets_json = json.dumps(detected_sheets, ensure_ascii=False)
    return df


def _pick_best_sheet(excel: pd.ExcelFile) -> str:
    best_sheet = excel.sheet_names[0]
    best_score = -1
    for sheet in excel.sheet_names:
        try:
            sample = pd.read_excel(excel, sheet_name=sheet, header=None, nrows=50)
            score = int(sample.notna().sum().sum())
            if score > best_score:
                best_score = score
                best_sheet = sheet
        except Exception:
            continue
    return best_sheet


# ---------------------------------------------------------------------------
# Save / load clean dataset
# ---------------------------------------------------------------------------

def save_clean_dataset(
    import_doc: Any,
    df: pd.DataFrame,
    schema: dict[str, Any],
    profile: dict[str, Any],
    mapping: dict[str, str],
    labels: dict[str, str],
    quality: dict[str, Any],
) -> Any:
    """Persist the cleaned dataset as a BI Clean Dataset record."""
    doc = frappe.new_doc("BI Clean Dataset")
    doc.dataset_title = import_doc.dataset_title or import_doc.dataset_name or "Jeu de données"
    doc.source_import = import_doc.name
    doc.quality_score = float(quality.get("score", 0))
    doc.row_count = int(len(df.index))
    doc.column_count = int(len(df.columns))
    doc.created_on = now_datetime()
    doc.schema_json = json.dumps(schema, ensure_ascii=False, default=str)
    doc.column_mapping_json = json.dumps(mapping, ensure_ascii=False)
    doc.column_labels_json = json.dumps(labels, ensure_ascii=False)
    doc.profile_json = json.dumps(profile, ensure_ascii=False, default=str)
    doc.preview_json = json.dumps(
        dataframe_preview(df, limit=50), ensure_ascii=False, default=str
    )
    doc.clean_data_json = json.dumps(
        df.where(pd.notnull(df), None).to_dict(orient="records"),
        ensure_ascii=False,
        default=str,
    )
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    logger.info(
        "pipeline: dataset nettoyé sauvegardé — %d lignes, %d colonnes, qualité=%s",
        doc.row_count,
        doc.column_count,
        doc.quality_score,
    )
    return doc


def load_clean_dataframe(clean_dataset_doc: Any) -> pd.DataFrame:
    """Rehydrate a pandas DataFrame from a BI Clean Dataset doc."""
    raw = clean_dataset_doc.clean_data_json or "[]"
    records = json.loads(raw)
    return pd.DataFrame(records)


def _unique_bi_dataset_name(base: str | None, current_name: str | None = None) -> str:
    base = (base or "Jeu de données").strip()[:120] or "Jeu de données"
    title = base
    counter = 2
    while True:
        existing = frappe.db.get_value("BI Dataset", {"dataset_name": title}, "name")
        if not existing or existing == current_name:
            return title
        title = f"{base} {counter}"
        counter += 1


def _legacy_detected_type(value: str | None) -> str:
    return {
        "number": "Number",
        "currency": "Currency",
        "date": "Date",
        "category": "Category",
        "text": "Text",
        "boolean": "Boolean",
        "identifier": "Identifier",
        "unknown": "Unknown",
    }.get(str(value or "").lower(), "Unknown")


def _legacy_semantic_role(value: str | None) -> str:
    return {
        "measure": "Measure",
        "dimension": "Dimension",
        "date": "Date Dimension",
        "identifier": "Identifier",
        "attribute": "Attribute",
        "unknown": "Ignored",
    }.get(str(value or "").lower(), "Attribute")


def _legacy_data_type(detected_type: str) -> str:
    if detected_type in {"Number", "Currency"}:
        return "Number"
    if detected_type == "Date":
        return "Date"
    return "Text"


def _profiles_from_clean_schema(schema: dict[str, Any]) -> list[SimpleNamespace]:
    profiles: list[SimpleNamespace] = []
    for column in schema.get("columns") or []:
        name = column.get("name")
        if not name:
            continue
        detected_type = _legacy_detected_type(column.get("type") or column.get("detected_type"))
        semantic_role = _legacy_semantic_role(column.get("semantic_type") or column.get("semantic_role"))
        data_type = _legacy_data_type(detected_type)
        unique_count = int(column.get("unique_count") or 0)
        profiles.append(
            SimpleNamespace(
                name=name,
                label=column.get("label") or name.replace("_", " ").title(),
                data_type=data_type,
                detected_type=detected_type,
                semantic_role=semantic_role,
                aggregation="sum" if semantic_role == "Measure" else "",
                include=True,
                is_numeric=data_type == "Number",
                is_category=semantic_role in {"Dimension", "Attribute"} and unique_count <= 100,
                is_date=data_type == "Date",
                distinct_count=unique_count,
                unique_count=unique_count,
                null_count=int(column.get("missing_count") or 0),
                null_rate=float(column.get("missing_rate") or 0),
                sample_values=column.get("sample_values") or [],
                conversion_errors=[],
                warnings=column.get("warnings") or [],
            )
        )
    return profiles


def _append_dataset_columns(dataset: Any, profiles: list[SimpleNamespace]) -> None:
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
                "include_in_analysis": 1,
                "display_order": index,
                "is_numeric": int(profile.is_numeric),
                "is_category": int(profile.is_category),
                "is_date": int(profile.is_date),
                "distinct_count": profile.distinct_count,
                "null_count": profile.null_count,
                "null_rate": profile.null_rate,
                "sample_values_json": json.dumps(profile.sample_values, ensure_ascii=False, default=str),
                "warnings": json.dumps({"warnings": profile.warnings}, ensure_ascii=False, default=str),
            },
        )


def _prepare_warehouse_dataframe(df: pd.DataFrame, profiles: list[SimpleNamespace]) -> pd.DataFrame:
    prepared = df.copy()
    columns = [profile.name for profile in profiles]
    prepared = prepared.reindex(columns=columns)
    for profile in profiles:
        if profile.name not in prepared.columns:
            continue
        if profile.data_type == "Date":
            prepared[profile.name] = pd.to_datetime(prepared[profile.name], errors="coerce").dt.date
        elif profile.data_type == "Number":
            prepared[profile.name] = pd.to_numeric(prepared[profile.name], errors="coerce")
    return prepared


def sync_bi_dataset_from_clean_dataset(clean_dataset_doc: Any, import_doc: Any | None = None) -> Any:
    """Mirror the modern BI Clean Dataset into BI Dataset for the Desk datasets view."""
    if not import_doc and clean_dataset_doc.source_import:
        import_doc = frappe.get_doc("BI Dataset Import", clean_dataset_doc.source_import)

    schema = json.loads(clean_dataset_doc.schema_json or "{}")
    profiles = _profiles_from_clean_schema(schema)
    if not profiles:
        frappe.throw("Schéma du dataset nettoyé vide: impossible de créer le BI Dataset.")

    existing_name = frappe.db.get_value("BI Dataset", {"source_import": clean_dataset_doc.source_import}, "name")
    dataset = frappe.get_doc("BI Dataset", existing_name) if existing_name else frappe.new_doc("BI Dataset")
    dataset.dataset_name = _unique_bi_dataset_name(clean_dataset_doc.dataset_title, dataset.name if existing_name else None)
    dataset.description = f"Dataset nettoyé depuis {clean_dataset_doc.source_import or clean_dataset_doc.name}"
    dataset.status = "Processing"
    dataset.source_file = getattr(import_doc, "import_file", None) if import_doc else None
    dataset.source_import = clean_dataset_doc.source_import
    dataset.row_count = int(clean_dataset_doc.row_count or 0)
    dataset.column_count = int(clean_dataset_doc.column_count or 0)
    dataset.quality_score = float(clean_dataset_doc.quality_score or 0)
    dataset.imported_at = clean_dataset_doc.created_on or now_datetime()
    dataset.last_transformed_on = now_datetime()
    dataset.clean_data_json = clean_dataset_doc.clean_data_json
    dataset.schema_json = clean_dataset_doc.schema_json
    owner_user = getattr(import_doc, "owner_user", None) if import_doc else None
    if owner_user:
        dataset.owner = owner_user
    _append_dataset_columns(dataset, profiles)

    if existing_name:
        dataset.save(ignore_permissions=True)
    else:
        dataset.insert(ignore_permissions=True)

    df = _prepare_warehouse_dataframe(load_clean_dataframe(clean_dataset_doc), profiles)
    create_analytical_tables(dataset, df, df, profiles)
    dataset.status = "Ready"
    dataset.save(ignore_permissions=True)
    frappe.db.commit()
    return dataset


@frappe.whitelist()
def sync_missing_bi_datasets() -> dict[str, Any]:
    """Backfill BI Dataset records for existing modern clean datasets."""
    synced = []
    skipped = []
    for row in frappe.get_all("BI Clean Dataset", fields=["name"], order_by="creation asc"):
        clean_doc = frappe.get_doc("BI Clean Dataset", row.name)
        if clean_doc.source_import and frappe.db.exists("BI Dataset", {"source_import": clean_doc.source_import}):
            skipped.append(clean_doc.name)
            continue
        dataset = sync_bi_dataset_from_clean_dataset(clean_doc)
        synced.append({"clean_dataset": clean_doc.name, "dataset": dataset.name})
    return {"synced": synced, "skipped": skipped}


# ---------------------------------------------------------------------------
# Dashboard spec building (Cohere output → validated dashboard.v1)
# ---------------------------------------------------------------------------

def _build_validated_dashboard_from_intent(
    prompt_payload: dict[str, Any],
    spec_doc: Any,
    dataset_schema: dict[str, Any],
    user_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a valid dashboard.v1 from Cohere's response, with anti-redundancy.

    Strategy:
    1. Try to interpret spec_doc.response_json as a full dashboard.v1 spec.
       If valid → apply anti-redundancy → return.
    2. Otherwise, parse as small intent (legacy) → build deterministically →
       apply anti-redundancy → return.
    3. If deterministic build also fails → raise DashboardValidationException.
    """
    from bi_studio.api.dashboard_normalizer import replace_redundant_widgets
    from bi_studio.utils.dashboard_intent import (
        build_dashboard_definition_from_intent,
        parse_dashboard_intent,
    )
    from bi_studio.utils.json_schema import (
        DashboardValidationException,
        prepare_dashboard_spec,
        validate_dashboard_spec,
    )

    fallback_title = (
        (prompt_payload.get("metadata") or {}).get("dashboard_objective") or "Dashboard"
    )
    column_metadata = dataset_schema.get("columns") or []

    # --- Path 1: Cohere returned a full dashboard.v1 spec ---
    raw_response = spec_doc.response_json or spec_doc.raw_response_text or ""
    try:
        parsed = json.loads(raw_response) if isinstance(raw_response, str) else raw_response
    except Exception:
        parsed = {}

    if isinstance(parsed, dict) and parsed.get("schema_version") == "dashboard.v1":
        prepared = prepare_dashboard_spec(parsed, dataset_schema)
        errors = validate_dashboard_spec(prepared, dataset_schema)
        if not errors:
            logger.info(
                "pipeline: spécification Cohere dashboard.v1 valide — %d widgets",
                len(prepared.get("widgets") or []),
            )
            normalized = replace_redundant_widgets(prepared, column_metadata, user_intent)
            errors_after = validate_dashboard_spec(normalized, dataset_schema)
            if not errors_after:
                return normalized
            logger.warning(
                "pipeline: %d erreur(s) après normalisation anti-redondance, "
                "retour au spec préparé",
                len(errors_after),
            )
            return prepared
        else:
            logger.warning(
                "pipeline: spécification Cohere invalide (%d erreur(s)) — "
                "fallback intent déterministe: %s",
                len(errors),
                errors[:3],
            )

    # --- Path 2: Parse as small intent + deterministic build (fallback) ---
    intent = (
        parse_dashboard_intent(spec_doc.response_json or "")
        or parse_dashboard_intent(spec_doc.raw_response_text or "")
    )
    dashboard = build_dashboard_definition_from_intent(
        intent, dataset_schema, fallback_title=fallback_title
    )
    dashboard = replace_redundant_widgets(dashboard, column_metadata, user_intent)
    errors = validate_dashboard_spec(dashboard, dataset_schema)

    if errors:
        logger.warning(
            "pipeline: fallback intent invalide — reconstruire depuis intent vide"
        )
        dashboard = build_dashboard_definition_from_intent(
            {}, dataset_schema, fallback_title=fallback_title
        )
        dashboard = replace_redundant_widgets(dashboard, column_metadata, user_intent)
        errors = validate_dashboard_spec(dashboard, dataset_schema)

    if errors:
        raise DashboardValidationException(errors)

    return dashboard


# ---------------------------------------------------------------------------
# Whitelisted entrypoint: upload → ETL (stops at "ETL Complete")
# ---------------------------------------------------------------------------

@frappe.whitelist()
def run_excel_to_dashboard_pipeline(
    file_url: str,
    dataset_title: str | None = None,
    sheet_name: str | None = None,
    header_row: int | None = None,
) -> dict[str, str]:
    """Create a BI Dataset Import doc and enqueue the ETL job (steps 1-5).

    The pipeline pauses at status="ETL Complete" and waits for the frontend
    to submit user intent via submit_user_intent_and_generate().
    """
    if not file_url:
        frappe.throw("Le fichier Excel est obligatoire.")

    doc = frappe.new_doc("BI Dataset Import")
    doc.import_file = file_url
    doc.dataset_title = dataset_title or "Jeu de données"
    doc.dataset_name = dataset_title or f"Import {now_datetime().strftime('%Y%m%d%H%M%S')}"
    doc.original_filename = file_url.rsplit("/", 1)[-1]
    doc.owner_user = frappe.session.user
    doc.selected_sheet = sheet_name or ""
    doc.header_row = int(header_row) if header_row else 0
    doc.status = "Uploaded"
    doc.insert(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "bi_studio.api.excel_pipeline.excel_to_etl_job",
        queue=PIPELINE_QUEUE,
        timeout=600,
        import_name=doc.name,
    )

    return {"import_name": doc.name, "status": "Uploaded"}


@frappe.whitelist()
def submit_user_intent_and_generate(
    import_name: str,
    user_intent_json: str = "{}",
) -> dict[str, str]:
    """Accept user intent and enqueue steps 6-8 (Cohere + Validate + Build).

    Called by the frontend after the user selects their analysis goals, KPIs,
    and preferred dimensions on the ETL Complete screen.

    Args:
        import_name:      Name of the BI Dataset Import doc (ETL already done).
        user_intent_json: JSON string with keys:
            analysis_goals, preferred_kpis, preferred_dimensions,
            preferred_visualizations (all optional).
    """
    doc = frappe.get_doc("BI Dataset Import", import_name)
    if not doc.clean_dataset:
        frappe.throw("ETL non terminé. Attendez la fin du nettoyage avant de soumettre.")
    if doc.status not in {"ETL Complete", "Failed"}:
        frappe.throw(
            f"Statut inattendu '{doc.status}'. L'ETL doit être terminé pour lancer l'IA."
        )

    try:
        user_intent = (
            json.loads(user_intent_json)
            if isinstance(user_intent_json, str)
            else (user_intent_json or {})
        )
        if not isinstance(user_intent, dict):
            user_intent = {}
    except Exception:
        user_intent = {}

    doc.status = "Waiting AI"
    doc.error_message = ""
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "bi_studio.api.excel_pipeline._generate_dashboard_from_intent_job",
        queue=PIPELINE_QUEUE,
        timeout=600,
        import_name=import_name,
        user_intent=user_intent,
    )
    return {"import_name": import_name, "status": "Waiting AI"}


# ---------------------------------------------------------------------------
# Background job: ETL (steps 1-5)
# ---------------------------------------------------------------------------

def excel_to_etl_job(import_name: str) -> None:
    """Background job: extract → clean → rename → profile → save clean dataset.

    Sets status="ETL Complete" on success. The frontend then shows the user
    intent form; after submission, _generate_dashboard_from_intent_job() runs.
    """
    import_doc = frappe.get_doc("BI Dataset Import", import_name)

    def _fail(step: str, exc: Exception) -> None:
        log_step(
            import_name, step, "Failed",
            message=str(exc), traceback_text=traceback.format_exc(),
        )
        import_doc.reload()
        import_doc.status = "Failed"
        import_doc.error_message = f"[{step}] {exc}"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()

    # --- 1. Extract ---------------------------------------------------------
    try:
        log_step(import_name, "Extract", "Started")
        import_doc.status = "Extracting"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()
        df_raw = extract_excel(import_doc)
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()
        log_step(
            import_name, "Extract", "Success",
            message=f"{import_doc.row_count_raw} lignes, {import_doc.column_count_raw} colonnes",
        )
    except Exception as exc:
        return _fail("Extract", exc)

    # --- 2. Clean -----------------------------------------------------------
    try:
        log_step(import_name, "Clean", "Started")
        import_doc.status = "Cleaning"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()
        cleaning = clean_dataset(df_raw)
        df_clean = cleaning["dataframe"]
        log_step(
            import_name, "Clean", "Success",
            message="\n".join(cleaning.get("log") or []) or "Nettoyage terminé.",
        )
    except Exception as exc:
        return _fail("Clean", exc)

    # --- 3. Rename columns --------------------------------------------------
    try:
        log_step(import_name, "Rename Columns", "Started")
        renamed = rename_columns_to_readable_names(df_clean)
        df_clean = renamed["dataframe"]
        mapping = renamed["column_mapping"]
        labels = renamed["column_labels"]
        log_step(
            import_name, "Rename Columns", "Success",
            message=f"{len(mapping)} colonnes renommées",
        )
    except Exception as exc:
        return _fail("Rename Columns", exc)

    # --- 4. Profile ---------------------------------------------------------
    try:
        log_step(import_name, "Profile", "Started")
        import_doc.status = "Profiling"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()
        profile = profile_dataset(df_clean, mapping, labels)
        schema = schema_from_profile(profile)
        quality = compute_dataset_quality(
            df_clean,
            profile_columns=profile.get("columns"),
            conversion_errors=cleaning.get("conversion_errors"),
        )
        logger.info(
            "pipeline: profil terminé — %d colonnes, score qualité=%s",
            len(profile.get("columns") or []),
            quality.get("score"),
        )
        log_step(
            import_name, "Profile", "Success",
            message=f"Score qualité: {quality.get('score')} | {len(profile.get('columns', []))} colonnes profilées",
        )
    except Exception as exc:
        return _fail("Profile", exc)

    # --- 5. Save clean dataset ----------------------------------------------
    try:
        clean_dataset_doc = save_clean_dataset(
            import_doc, df_clean, schema, profile, mapping, labels, quality,
        )
        dataset_doc = sync_bi_dataset_from_clean_dataset(clean_dataset_doc, import_doc)
        import_doc.reload()
        import_doc.clean_dataset = clean_dataset_doc.name
        import_doc.row_count_cleaned = int(len(df_clean.index))
        import_doc.column_count_cleaned = int(len(df_clean.columns))
        import_doc.cleaned_preview_json = json.dumps(
            dataframe_preview(df_clean, limit=50),
            ensure_ascii=False, default=str,
        )
        import_doc.status = "ETL Complete"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()
        log_step(
            import_name, "ETL Complete", "Success",
            message=(
                f"Dataset nettoyé: {clean_dataset_doc.name} | "
                f"Dataset BI: {dataset_doc.name} | "
                f"{import_doc.row_count_cleaned} lignes | "
                f"{import_doc.column_count_cleaned} colonnes"
            ),
        )
    except Exception as exc:
        return _fail("Profile", exc)


# ---------------------------------------------------------------------------
# Background job: Cohere + Validate + Build (steps 6-8)
# ---------------------------------------------------------------------------

def _generate_dashboard_from_intent_job(
    import_name: str,
    user_intent: dict[str, Any] | None = None,
) -> None:
    """Background job: run Cohere → validate spec → anti-redundancy → build dashboard.

    Args:
        import_name: Name of the BI Dataset Import doc (ETL already complete).
        user_intent: User analysis choices collected by the frontend.
    """
    from bi_studio.api.cohere_dashboard import (
        build_cohere_dashboard_prompt,
        call_cohere_dashboard_generation,
        create_fallback_dashboard_spec,
    )
    from bi_studio.api.dashboard_builder import build_dashboard_from_spec

    user_intent = user_intent or {}
    import_doc = frappe.get_doc("BI Dataset Import", import_name)
    clean_dataset_doc = frappe.get_doc("BI Clean Dataset", import_doc.clean_dataset)
    schema = json.loads(clean_dataset_doc.schema_json or "{}")
    spec_doc = None

    def _fail(step: str, exc: Exception) -> None:
        log_step(
            import_name, step, "Failed",
            message=str(exc), traceback_text=traceback.format_exc(),
        )
        import_doc.reload()
        import_doc.status = "Failed"
        import_doc.error_message = f"[{step}] {exc}"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()

    # --- 6. Cohere prompt + call --------------------------------------------
    try:
        log_step(import_name, "Cohere Prompt", "Started")
        import_doc.status = "Waiting AI"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()

        prompt_payload = build_cohere_dashboard_prompt(
            clean_dataset_doc, user_intent=user_intent
        )
        try:
            spec_doc, spec_response = call_cohere_dashboard_generation(
                prompt_payload,
                clean_dataset_doc=clean_dataset_doc,
                import_doc=import_doc,
            )
            widget_count = len((spec_response or {}).get("widgets") or [])
            message = (
                f"Spécification Cohere reçue — "
                f"{'dashboard.v1' if spec_response.get('schema_version') else 'intent'} | "
                f"{widget_count} widgets"
            )
            logger.info("pipeline: %s", message)
        except Exception as cohere_exc:
            frappe.log_error(
                title="BI Pipeline: fallback spec déterministe",
                message=traceback.format_exc(),
            )
            spec_doc, spec_response = create_fallback_dashboard_spec(
                prompt_payload,
                clean_dataset_doc=clean_dataset_doc,
                import_doc=import_doc,
                reason=str(cohere_exc),
            )
            message = f"IA indisponible, spec déterministe: {cohere_exc}"
            logger.warning("pipeline: %s", message)

        log_step(import_name, "Cohere Prompt", "Success", message=message[:5000])
    except Exception as exc:
        return _fail("Cohere Prompt", exc)

    # --- 7. Validate + anti-redundancy --------------------------------------
    try:
        log_step(import_name, "Validate AI JSON", "Started")
        spec_json = _build_validated_dashboard_from_intent(
            prompt_payload, spec_doc, schema, user_intent=user_intent
        )
        final_widget_count = len(spec_json.get("widgets") or [])
        spec_doc.validation_status = "Valid"
        spec_doc.validated_json = json.dumps(spec_json, ensure_ascii=False, default=str)
        spec_doc.save(ignore_permissions=True)
        frappe.db.commit()
        log_step(
            import_name, "Validate AI JSON", "Success",
            message=f"JSON validé — {final_widget_count} widgets finaux",
        )
        logger.info("pipeline: dashboard.v1 validé — %d widgets", final_widget_count)
    except Exception as exc:
        if spec_doc:
            errors = getattr(exc, "errors", None)
            if errors:
                spec_doc.validation_status = "Invalid"
                spec_doc.validation_errors = "\n".join(errors[:20])
                spec_doc.save(ignore_permissions=True)
                frappe.db.commit()
            logger.error("pipeline: validation échouée — %d erreur(s)", len(errors or []))
        return _fail("Validate AI JSON", exc)

    # --- 8. Build dashboard -------------------------------------------------
    try:
        log_step(import_name, "Build Dashboard", "Started")
        dashboard_doc = build_dashboard_from_spec(clean_dataset_doc, spec_doc)
        import_doc.reload()
        import_doc.created_dashboard = dashboard_doc.name
        import_doc.ai_dashboard_spec = spec_doc.name
        import_doc.status = "Dashboard Ready"
        import_doc.save(ignore_permissions=True)
        frappe.db.commit()
        log_step(import_name, "Build Dashboard", "Success", message=dashboard_doc.name)
        logger.info("pipeline: dashboard créé — %s", dashboard_doc.name)
    except Exception as exc:
        return _fail("Build Dashboard", exc)


# ---------------------------------------------------------------------------
# Helpers exposed to the frontend
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_pipeline_status(import_name: str) -> dict[str, Any]:
    """Return the current pipeline state + recent ETL logs for the UI.

    When status is "ETL Complete", the response also includes column_metadata
    so the frontend can populate the user intent form with the actual columns.
    """
    if not import_name:
        frappe.throw("Import name requis.")
    doc = frappe.get_doc("BI Dataset Import", import_name)
    logs = frappe.get_all(
        "BI ETL Job Log",
        filters={"dataset_import": import_name},
        fields=["name", "job_type", "status", "started_on", "finished_on", "log_message"],
        order_by="creation asc",
        limit=200,
    )
    result: dict[str, Any] = {
        "name": doc.name,
        "status": doc.status,
        "dataset_title": doc.dataset_title,
        "row_count_raw": doc.row_count_raw,
        "row_count_cleaned": doc.row_count_cleaned,
        "column_count_raw": doc.column_count_raw,
        "column_count_cleaned": doc.column_count_cleaned,
        "clean_dataset": doc.clean_dataset,
        "ai_dashboard_spec": doc.ai_dashboard_spec,
        "created_dashboard": doc.created_dashboard,
        "error_message": doc.error_message,
        "logs": logs,
    }

    # Include column metadata when ETL is complete so the frontend can build
    # the user intent form with actual column names and types.
    if doc.status in {"ETL Complete", "Waiting AI", "Dashboard Ready"} and doc.clean_dataset:
        try:
            clean_doc = frappe.get_doc("BI Clean Dataset", doc.clean_dataset)
            schema = json.loads(clean_doc.schema_json or "{}")
            result["column_metadata"] = schema.get("columns") or []
        except Exception:
            result["column_metadata"] = []

    return result


@frappe.whitelist()
def retry_ai_generation(import_name: str) -> dict[str, str]:
    """Re-run steps 6-8 from an existing clean dataset.

    Recovers user_intent from the stored prompt_json of the last spec doc.
    """
    doc = frappe.get_doc("BI Dataset Import", import_name)
    if not doc.clean_dataset:
        frappe.throw("Le jeu de données nettoyé est manquant. Relancer le pipeline complet.")

    # Recover user_intent from the most recent spec doc if available
    user_intent: dict[str, Any] = {}
    if doc.ai_dashboard_spec:
        try:
            prev_spec = frappe.get_doc("BI AI Dashboard Spec", doc.ai_dashboard_spec)
            prev_payload = json.loads(prev_spec.prompt_json or "{}")
            user_intent = prev_payload.get("user_intent") or {}
        except Exception:
            pass

    doc.status = "Waiting AI"
    doc.error_message = ""
    doc.save(ignore_permissions=True)
    frappe.db.commit()

    frappe.enqueue(
        "bi_studio.api.excel_pipeline._generate_dashboard_from_intent_job",
        queue=PIPELINE_QUEUE,
        timeout=600,
        import_name=import_name,
        user_intent=user_intent,
    )
    return {"import_name": import_name, "status": "Waiting AI"}


# ---------------------------------------------------------------------------
# Legacy alias kept for backward compatibility with already-queued jobs
# ---------------------------------------------------------------------------

def excel_to_dashboard_job(import_name: str) -> None:
    """Legacy alias: runs ETL then immediately generates the dashboard.

    Kept for already-queued background jobs. New code uses
    excel_to_etl_job() + _generate_dashboard_from_intent_job() separately.
    """
    excel_to_etl_job(import_name)
    import_doc = frappe.get_doc("BI Dataset Import", import_name)
    if import_doc.status == "ETL Complete" and import_doc.clean_dataset:
        _generate_dashboard_from_intent_job(import_name, user_intent={})


def _retry_ai_job(import_name: str) -> None:
    """Legacy alias: use _generate_dashboard_from_intent_job instead."""
    _generate_dashboard_from_intent_job(import_name, user_intent={})
