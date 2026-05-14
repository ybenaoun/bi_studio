"""Rename raw Excel column headers to clean snake_case names + French labels.

Handles:
- common business synonyms ("DOB" -> "date_of_birth", "Dept" -> "department")
- accent stripping
- snake_case normalization
- collision resolution (foo, foo_2, foo_3)
- mapping {original_name -> normalized_name}
- French label mapping {normalized_name -> French label} for the UI
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

import pandas as pd


# Common rename hints. Keys are normalized lowercase tokens, values are the
# preferred snake_case name. Order matters: specific -> general.
RENAME_HINTS: dict[str, str] = {
    "no": "employee_id",
    "n_": "employee_id",
    "matricule": "employee_id",
    "id": "employee_id",
    "name": "employee_name",
    "full name": "employee_name",
    "nom": "employee_name",
    "nom complet": "employee_name",
    "gender": "gender",
    "genre": "gender",
    "sex": "gender",
    "department": "department",
    "dept": "department",
    "departement": "department",
    "service": "department",
    "start date": "start_date",
    "joining date": "start_date",
    "date debut": "start_date",
    "date d embauche": "start_date",
    "hire date": "start_date",
    "end date": "end_date",
    "monthly salary": "monthly_salary",
    "salary monthly": "monthly_salary",
    "salaire mensuel": "monthly_salary",
    "annual salary": "annual_salary",
    "salaire annuel": "annual_salary",
    "dob": "date_of_birth",
    "date of birth": "date_of_birth",
    "date naissance": "date_of_birth",
    "email": "email",
    "phone": "phone",
    "telephone": "phone",
    "address": "address",
    "adresse": "address",
}

# French labels for common normalized columns
FRENCH_LABELS: dict[str, str] = {
    "employee_id": "Identifiant employé",
    "employee_name": "Nom de l'employé",
    "gender": "Genre",
    "department": "Département",
    "start_date": "Date de début",
    "end_date": "Date de fin",
    "monthly_salary": "Salaire mensuel",
    "annual_salary": "Salaire annuel",
    "date_of_birth": "Date de naissance",
    "email": "Adresse e-mail",
    "phone": "Téléphone",
    "address": "Adresse",
    "country": "Pays",
    "city": "Ville",
    "amount": "Montant",
    "total": "Total",
    "quantity": "Quantité",
    "price": "Prix",
    "cost": "Coût",
    "revenue": "Chiffre d'affaires",
    "customer": "Client",
    "product": "Produit",
}


def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _to_snake_case(text: str) -> str:
    text = _strip_accents(str(text or "")).lower().strip()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "column"


def _french_label_from(snake: str) -> str:
    if snake in FRENCH_LABELS:
        return FRENCH_LABELS[snake]
    # Heuristic: replace _ with space, capitalize
    return snake.replace("_", " ").capitalize()


def _resolve_hint(raw_label: str) -> str | None:
    key = _strip_accents(str(raw_label or "")).lower().strip()
    key = re.sub(r"[^a-z0-9 ]+", " ", key)
    key = re.sub(r"\s+", " ", key).strip()
    return RENAME_HINTS.get(key)


def rename_columns_to_readable_names(df: pd.DataFrame) -> dict[str, Any]:
    """Rename df columns to snake_case with collision resolution.

    Returns:
        {
            "dataframe": df with renamed columns,
            "column_mapping": {original_name: normalized_name},
            "column_labels": {normalized_name: French label}
        }
    """
    if df is None or df.empty:
        return {"dataframe": df, "column_mapping": {}, "column_labels": {}}

    new_cols: list[str] = []
    mapping: dict[str, str] = {}
    labels: dict[str, str] = {}
    seen: dict[str, int] = {}

    for original in df.columns:
        original_str = str(original)
        candidate = _resolve_hint(original_str) or _to_snake_case(original_str)

        # Collision resolution
        base = candidate
        if base in seen:
            seen[base] += 1
            candidate = f"{base}_{seen[base]}"
        else:
            seen[base] = 1

        new_cols.append(candidate)
        mapping[original_str] = candidate
        labels[candidate] = _french_label_from(candidate)

    df_renamed = df.copy()
    df_renamed.columns = new_cols
    return {
        "dataframe": df_renamed,
        "column_mapping": mapping,
        "column_labels": labels,
    }
