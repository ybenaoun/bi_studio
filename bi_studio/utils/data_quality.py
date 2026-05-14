"""Compute a real data quality score from a cleaned dataframe + profile.

Builds on top of `bi_studio.utils.quality.calculate_quality_score` but adds:
- penalty for invalid date conversions
- penalty for constant columns
- richer breakdown returned to the UI

NEVER returns a hardcoded 100. The score is always derived from the profile.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from bi_studio.utils.quality import calculate_quality_score


def compute_dataset_quality(
    df: pd.DataFrame,
    profile_columns: list[dict[str, Any]] | None = None,
    conversion_errors: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Compute a quality score for a cleaned dataset.

    Inputs:
        df: cleaned pandas DataFrame
        profile_columns: list of dicts produced by dataset_profiler.profile_dataset
        conversion_errors: optional {column_name: count} dict from etl_cleaning

    Returns a dict with score, breakdown, and warnings.
    """
    profile_columns = profile_columns or []
    conversion_errors = conversion_errors or {}

    constant_columns = [
        col for col in profile_columns
        if "constant_column" in (col.get("warnings") or [])
    ]
    empty_columns = [
        col for col in profile_columns
        if "empty_column" in (col.get("warnings") or [])
    ]
    invalid_date_columns = [
        col for col in profile_columns
        if "partial_date_conversion" in (col.get("warnings") or [])
    ]
    unusable = len(constant_columns) + len(empty_columns)

    base = calculate_quality_score(
        df,
        conversion_errors=conversion_errors,
        unusable_columns=unusable,
    )

    # Extra penalty for invalid date conversions (capped)
    invalid_date_penalty = min(10, len(invalid_date_columns) * 2.5)
    final_score = max(0.0, round(base["score"] - invalid_date_penalty, 2))

    return {
        "score": final_score,
        "missing_rate": base["missing_rate"],
        "duplicate_rate": base["duplicate_rate"],
        "conversion_error_rate": base["conversion_error_rate"],
        "conversion_errors": base["conversion_errors"],
        "unusable_columns": base["unusable_columns"],
        "constant_columns": [c.get("name") for c in constant_columns],
        "empty_columns": [c.get("name") for c in empty_columns],
        "invalid_date_columns": [c.get("name") for c in invalid_date_columns],
        "invalid_date_penalty": invalid_date_penalty,
    }
