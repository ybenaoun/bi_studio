"""Deprecated legacy Excel import helpers.

The intelligent pipeline now lives in:
- bi_studio.api.importer
- bi_studio.api.profiling
- bi_studio.api.etl
- bi_studio.api.excel_pipeline

This module is kept for backward compatibility with legacy dataset imports.
"""
import os
from dataclasses import dataclass

import frappe
import pandas as pd
from frappe import _
from frappe.utils import now_datetime

from bi_studio.config import ALLOWED_IMPORT_EXTENSIONS, MAX_IMPORT_ROWS
from bi_studio.services.naming import dedupe_names
from bi_studio.services.serialization import dataframe_preview, to_json
from bi_studio.utils.deprecation import warn_deprecated


DEPRECATED = True
DEPRECATION_MESSAGE = (
    "bi_studio.services.etl is a legacy import/cleaning module. "
    "Use the intelligent Excel workflow modules in bi_studio.api.importer, "
    "bi_studio.api.profiling and bi_studio.api.etl."
)


@dataclass
class ColumnProfile:
    """Profil descriptif d'une colonne après analyse : type détecté, statistiques et exemples de valeurs."""
    label: str
    name: str
    data_type: str
    is_numeric: bool
    is_category: bool
    is_date: bool
    distinct_count: int
    null_count: int
    sample_values: list


def validate_extension(file_name):
    """Vérifie que l'extension du fichier fait partie des formats autorisés (xlsx, xls).
    Lève une exception Frappe si l'extension est invalide.
    """
    extension = os.path.splitext(file_name or "")[1].lower()
    if extension not in ALLOWED_IMPORT_EXTENSIONS:
        frappe.throw(_("Seuls les fichiers Excel .xlsx et .xls sont autorisés."))
    return extension


def resolve_file(file_url=None, file_name=None):
    """Recherche le document File dans Frappe à partir de son nom ou de son URL.
    Valide l'extension du fichier, puis retourne le doc et son chemin absolu sur disque.
    """
    filters = {}
    if file_name:
        filters["name"] = file_name
    elif file_url:
        filters["file_url"] = file_url
    else:
        frappe.throw(_("Aucun fichier n'a été fourni."))

    file_doc_name = frappe.db.get_value("File", filters, "name")
    if not file_doc_name:
        frappe.throw(_("Fichier introuvable."))

    file_doc = frappe.get_doc("File", file_doc_name)
    validate_extension(file_doc.file_name or file_doc.file_url)
    return file_doc, file_doc.get_full_path()


def read_excel_file(path):
    """Lit un fichier Excel depuis le chemin donné et retourne un DataFrame brut.
    Rejette les fichiers vides ou dépassant la limite MAX_IMPORT_ROWS.
    """
    warn_deprecated("bi_studio.services.etl.read_excel_file", "bi_studio.api.importer")
    df = pd.read_excel(path, dtype=object)
    if len(df.index) > MAX_IMPORT_ROWS:
        frappe.throw(_(f"Le fichier dépasse la limite autorisée de {MAX_IMPORT_ROWS} lignes."))
    if len(df.columns) == 0:
        frappe.throw(_("Le fichier ne contient aucune colonne exploitable."))
    return df


def preview_excel(file_url=None, file_name=None):
    """Point d'entrée pour la prévisualisation d'un fichier Excel.
    Résout le fichier, le lit, le nettoie et retourne un résumé (dimensions, profils de colonnes, aperçu des données).
    """
    warn_deprecated("bi_studio.services.etl.preview_excel", "bi_studio.api.importer.upload_and_preview_excel")
    file_doc, path = resolve_file(file_url=file_url, file_name=file_name)
    df = read_excel_file(path)
    clean_df, profiles = clean_dataframe(df)
    return {
        "file": file_doc.name,
        "file_url": file_doc.file_url,
        "file_name": file_doc.file_name,
        "total_rows": len(clean_df.index),
        "total_columns": len(clean_df.columns),
        "columns": [profile.__dict__ for profile in profiles],
        "preview": dataframe_preview(clean_df),
    }


def clean_dataframe(raw_df):
    """Nettoie le DataFrame brut et produit un profil pour chaque colonne.

    Étapes :
    - Déduplique les noms de colonnes.
    - Supprime les espaces et normalise les valeurs nulles dans les colonnes texte.
    - Supprime les doublons de lignes.
    - Détecte le type de chaque colonne (Number / Date / Text) par ratio de conversion.
    - Détermine si une colonne est catégorielle selon sa cardinalité.

    Retourne le DataFrame nettoyé et la liste des ColumnProfile correspondants.
    """
    warn_deprecated("bi_studio.services.etl.clean_dataframe", "bi_studio.api.etl_cleaning.clean_dataset")
    df = raw_df.copy()
    df.columns = dedupe_names(df.columns)

    for column in df.columns:
        if df[column].dtype == object:
            df[column] = df[column].map(lambda value: value.strip() if isinstance(value, str) else value)
            df[column] = df[column].replace({"": None, "nan": None, "NaN": None, "NULL": None, "null": None})

    df = df.drop_duplicates().reset_index(drop=True)
    profiles = []

    for column in df.columns:
        original = raw_df.columns[list(df.columns).index(column)]
        series = df[column]
        converted_numeric = pd.to_numeric(series, errors="coerce")
        converted_date = pd.to_datetime(series, errors="coerce")

        non_null_count = int(series.notna().sum())
        numeric_ratio = float(converted_numeric.notna().sum() / non_null_count) if non_null_count else 0
        date_ratio = float(converted_date.notna().sum() / non_null_count) if non_null_count else 0

        # Classement par priorité : Number (≥70 %) > Date (≥60 %) > Text
        if non_null_count and numeric_ratio >= 0.7:
            df[column] = converted_numeric
            data_type = "Number"
            is_numeric = True
            is_date = False
        elif non_null_count and date_ratio >= 0.6:
            df[column] = converted_date.dt.date
            data_type = "Date"
            is_numeric = False
            is_date = True
        else:
            df[column] = series.where(pd.notnull(series), None).map(lambda value: str(value) if value is not None else None)
            data_type = "Text"
            is_numeric = False
            is_date = False

        distinct_count = int(df[column].nunique(dropna=True))
        # Catégorielle si valeurs peu nombreuses (texte à faible cardinalité) ou si colonne date
        is_category = (not is_numeric and not is_date and distinct_count <= min(100, max(20, len(df.index) // 2))) or (
            is_date
        )
        samples = [value for value in df[column].dropna().astype(str).head(5).tolist()]

        profiles.append(
            ColumnProfile(
                label=str(original),
                name=column,
                data_type=data_type,
                is_numeric=is_numeric,
                is_category=is_category,
                is_date=is_date,
                distinct_count=distinct_count,
                null_count=int(df[column].isna().sum()),
                sample_values=samples,
            )
        )

    return df.where(pd.notnull(df), None), profiles


def build_dataset_columns(dataset_doc, profiles):
    """Peuple le child table 'columns' du document dataset à partir des profils analysés.
    Réinitialise d'abord la liste existante, puis ajoute une ligne par profil dans l'ordre d'affichage.
    """
    warn_deprecated("bi_studio.services.etl.build_dataset_columns", "bi_studio.api.etl.append_dataset_columns")
    dataset_doc.set("columns", [])
    for index, profile in enumerate(profiles, start=1):
        dataset_doc.append(
            "columns",
            {
                "column_label": profile.label,
                "column_name": profile.name,
                "data_type": profile.data_type,
                "is_numeric": int(profile.is_numeric),
                "is_category": int(profile.is_category),
                "is_date": int(profile.is_date),
                "distinct_count": profile.distinct_count,
                "null_count": profile.null_count,
                "sample_values_json": to_json(profile.sample_values),
                "display_order": index,
            },
        )


def create_import_job(file_doc):
    """Crée et insère un document 'BI Import Job' pour tracer l'import en cours.
    Initialise le statut à 'Running' et horodate le démarrage.
    """
    warn_deprecated("bi_studio.services.etl.create_import_job", "BI Dataset Import + BI ETL Job Log")
    job = frappe.get_doc(
        {
            "doctype": "BI Import Job",
            "source_file": file_doc.file_url,
            "imported_by": frappe.session.user,
            "status": "Running",
            "started_at": now_datetime(),
        }
    )
    job.insert(ignore_permissions=True)
    return job
