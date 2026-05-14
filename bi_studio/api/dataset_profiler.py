"""Profile a cleaned dataset to produce a schema usable by the Cohere prompt.

For each column we compute:
- detected_type: number, currency, date, category, text, boolean, identifier, unknown
- semantic_type: measure, dimension, date, identifier, attribute
- missing_count, missing_rate
- unique_count, sample_values
- min/max for numeric or date
- mean for numeric measures
- most_frequent_values for categorical
- warnings (constant_column, partial_date_conversion, ...)

Reuses the proven type detection in `bi_studio.utils.data_types` but emits the
new English semantic_type names expected by the JSON schema validator.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from bi_studio.utils.data_types import detect_column_profile


# Map legacy semantic_role -> new lower-case semantic_type
_SEMANTIC_ROLE_MAP = {
    "Identifier": "identifier",
    "Measure": "measure",
    "Dimension": "dimension",
    "Date Dimension": "date",
    "Attribute": "attribute",
    "Ignored": "unknown",
}

_DETECTED_TYPE_MAP = {
    "Identifier": "identifier",
    "Number": "number",
    "Currency": "currency",
    "Date": "date",
    "Category": "category",
    "Text": "text",
    "Boolean": "boolean",
    "Unknown": "unknown",
}


def _column_profile(
    series: pd.Series,
    name: str,
    original_name: str,
    label: str,
    row_count: int,
) -> dict[str, Any]:
    legacy = detect_column_profile(series, original_name, name, row_count)

    detected = _DETECTED_TYPE_MAP.get(legacy.detected_type, "unknown")
    semantic = _SEMANTIC_ROLE_MAP.get(legacy.semantic_role, "unknown")

    most_frequent: list[Any] = []
    if semantic in {"dimension", "attribute"} and not series.empty:
        try:
            counts = series.value_counts(dropna=True).head(5)
            most_frequent = [
                {"value": str(idx), "count": int(cnt)}
                for idx, cnt in counts.items()
            ]
        except Exception:
            most_frequent = []

    return {
        "name": name,
        "original_name": original_name,
        "label": label,
        "type": detected,
        "detected_type": detected,
        "semantic_type": semantic,
        "semantic_role": semantic,
        "missing_count": int(legacy.null_count),
        "missing_rate": float(legacy.null_rate),
        "unique_count": int(legacy.unique_count),
        "sample_values": list(legacy.sample_values or []),
        "min": legacy.min_value,
        "max": legacy.max_value,
        "mean": legacy.mean_value,
        "most_frequent_values": most_frequent,
        "warnings": list(legacy.warnings or []),
        "include": bool(legacy.include),
        "confidence": float(legacy.confidence),
    }


def profile_dataset(
    df: pd.DataFrame,
    column_mapping: dict[str, str] | None = None,
    column_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Profile every column of the cleaned dataframe.

    Args:
        df: cleaned dataframe with snake_case column names
        column_mapping: {original_name: normalized_name}
        column_labels: {normalized_name: French label}

    Returns dict with `columns` list and dataset-level stats.
    """
    column_mapping = column_mapping or {}
    column_labels = column_labels or {}
    inverse_mapping = {v: k for k, v in column_mapping.items()}

    row_count = int(len(df.index))
    column_profiles: list[dict[str, Any]] = []
    for col in df.columns:
        name = str(col)
        original = inverse_mapping.get(name, name)
        label = column_labels.get(name, name.replace("_", " ").capitalize())
        column_profiles.append(
            _column_profile(df[col], name, original, label, row_count)
        )

    return {
        "row_count": row_count,
        "column_count": int(len(df.columns)),
        "columns": column_profiles,
    }


def schema_from_profile(profile: dict[str, Any]) -> dict[str, Any]:
    """Produce an enriched schema for the Cohere prompt + JSON validator.

    Each column entry contains at minimum:
      name, original_name, label, type, semantic_type, nullable,
      unique_count, sample_values, warnings
    Numeric columns also include: min, max, avg
    Temporal columns also include: date_min, date_max
    """
    columns = profile.get("columns") or []
    result: list[dict[str, Any]] = []
    for col in columns:
        if not col.get("include", True):
            continue
        col_type = str(col.get("type") or "").lower()
        entry: dict[str, Any] = {
            "name": col["name"],
            "original_name": col.get("original_name"),
            "label": col.get("label"),
            "type": col_type,
            "semantic_type": col.get("semantic_type"),
            "nullable": bool((col.get("missing_count") or 0) > 0),
            "missing_rate": col.get("missing_rate"),
            "unique_count": col.get("unique_count"),
            "sample_values": col.get("sample_values"),
            "warnings": list(col.get("warnings") or []),
        }
        if col_type in {"number", "currency"}:
            if col.get("min") is not None:
                entry["min"] = col["min"]
            if col.get("max") is not None:
                entry["max"] = col["max"]
            if col.get("mean") is not None:
                entry["avg"] = col["mean"]
        elif col_type == "date":
            if col.get("min") is not None:
                entry["date_min"] = str(col["min"])
            if col.get("max") is not None:
                entry["date_max"] = str(col["max"])
        result.append(entry)
    return {"columns": result}
