"""ETL cleaning step for the intelligent pipeline.

Replaces aggressive ad-hoc cleaning with a logged, auditable pipeline:
- standardize null markers
- strip whitespace in text columns
- parse numbers (handle thousand separators, currency symbols, parentheses,
  AND both US-style "5,500.00" and French-style "3 200,00" decimals)
- parse dates (without crashing on invalid)
- detect constants and empty columns (mark, do not drop silently)
- count conversion errors per column for the quality score
"""
from __future__ import annotations

import re
from typing import Any

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype

from bi_studio.utils.data_types import (
    normalise_missing,
    parse_date_series,
)


_CURRENCY_RE = re.compile(r"[$€£¥₦]|\b(usd|eur|gbp|ngn|xaf|xof)\b", re.IGNORECASE)


def _is_text_dtype(series: pd.Series) -> bool:
    return is_object_dtype(series.dtype) or is_string_dtype(series.dtype)


def _parse_locale_number(value: Any) -> float | None:
    """Parse a number that may be in US or French locale, with currency.

    "3 200,00"  -> 3200.00     (French decimals, space thousands)
    "5,500.00"  -> 5500.00     (US format)
    "$3,800"    -> 3800.0
    "(1 234,56)" -> -1234.56
    """
    value = normalise_missing(value)
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    text = _CURRENCY_RE.sub("", text)
    text = text.replace("%", "").strip()
    # Remove thin spaces and regular spaces used as thousand separators
    text = text.replace(" ", "").replace("\xa0", "").replace(" ", "")
    if not text:
        return None

    has_dot = "." in text
    has_comma = "," in text

    if has_dot and has_comma:
        # If dot comes before comma -> European format "1.234,56"
        if text.rfind(".") < text.rfind(","):
            text = text.replace(".", "").replace(",", ".")
        else:
            # US format "1,234.56" -> drop thousand commas
            text = text.replace(",", "")
    elif has_comma and not has_dot:
        comma_parts = text.split(",")
        if len(comma_parts) == 2 and len(comma_parts[1]) == 3 and comma_parts[1].isdigit():
            # US thousands without decimal part: "$3,800" -> "3800".
            text = "".join(comma_parts)
        elif len(comma_parts) > 2 and all(part.isdigit() and len(part) == 3 for part in comma_parts[1:]):
            # Repeated US thousands: "1,234,567" -> "1234567".
            text = "".join(comma_parts)
        else:
            # French decimal "3200,00" -> "3200.00".
            text = text.replace(",", ".")

    if negative and not text.startswith("-"):
        text = f"-{text}"

    try:
        return float(text)
    except ValueError:
        return None


def parse_number_series_locale(series: pd.Series) -> pd.Series:
    """Locale-aware number parser used by the intelligent ETL pipeline."""
    return series.map(_parse_locale_number)


def _strip_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if _is_text_dtype(df[col]):
            df[col] = df[col].map(lambda v: v.strip() if isinstance(v, str) else v)
    return df


def _drop_empty_axes(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Drop fully-empty rows + fully-empty columns. Return removed column names."""
    df = df.dropna(how="all").reset_index(drop=True)
    removed: list[str] = []
    for col in list(df.columns):
        if df[col].isna().all() or (df[col].astype(str).str.strip() == "").all():
            removed.append(str(col))
            df = df.drop(columns=[col])
    return df, removed


def _normalize_nulls(df: pd.DataFrame) -> pd.DataFrame:
    """Replace common null markers ('N/A', '-', 'null', 'None', ...) with NaN."""
    for col in df.columns:
        if _is_text_dtype(df[col]):
            df[col] = df[col].map(normalise_missing)
    return df


def clean_dataset(df: pd.DataFrame) -> dict[str, Any]:
    """Run the full ETL cleaning step.

    Returns:
        {
            "dataframe": cleaned_df,
            "conversion_errors": {col: int},
            "removed_empty_columns": [str],
            "removed_constant_columns": [str],
            "duplicate_rows_removed": int,
            "log": [str],
        }
    """
    log: list[str] = []
    if df is None or df.empty:
        return {
            "dataframe": df if df is not None else pd.DataFrame(),
            "conversion_errors": {},
            "removed_empty_columns": [],
            "removed_constant_columns": [],
            "duplicate_rows_removed": 0,
            "log": ["Aucune donnée fournie."],
        }

    df = df.copy()

    # 1. Drop fully empty rows / columns
    df, removed_empty = _drop_empty_axes(df)
    if removed_empty:
        log.append(f"Colonnes vides supprimées: {', '.join(removed_empty)}")

    # 2. Strip whitespace
    df = _strip_text_columns(df)

    # 3. Normalize null markers
    df = _normalize_nulls(df)

    # 4. Drop exact duplicates
    before_rows = len(df.index)
    df = df.drop_duplicates().reset_index(drop=True)
    duplicates_removed = before_rows - len(df.index)
    if duplicates_removed:
        log.append(f"Doublons exacts supprimés: {duplicates_removed}")

    # 5. Detect constant columns (do not drop, just flag)
    constants: list[str] = []
    for col in df.columns:
        non_null = df[col].dropna()
        if not non_null.empty and non_null.nunique() <= 1:
            constants.append(str(col))
    if constants:
        log.append(f"Colonnes constantes détectées: {', '.join(constants)}")

    # 6. Try numeric parsing for object columns and count conversion errors
    conversion_errors: dict[str, int] = {}
    for col in list(df.columns):
        if not _is_text_dtype(df[col]):
            continue
        series = df[col]
        non_null = series.dropna()
        if non_null.empty:
            continue
        # Heuristic: only try numeric if at least one value looks numeric
        looks_numeric_ratio = non_null.astype(str).str.match(
            r"^\s*-?\(?\s*[$€£¥₦]?\s*[\d\s.,\xa0]+%?\)?\s*$"
        ).mean()
        if looks_numeric_ratio >= 0.6:
            parsed = parse_number_series_locale(series)
            failures = int(((parsed.isna()) & (series.notna())).sum())
            success_ratio = float(parsed.notna().sum() / max(non_null.size, 1))
            if success_ratio >= 0.6:
                df[col] = pd.to_numeric(parsed, errors="coerce")
                if failures:
                    conversion_errors[str(col)] = failures
                    log.append(f"{failures} valeurs non numériques dans '{col}'")

    # 7. Try date parsing for columns whose name suggests dates
    date_keywords = ("date", "start", "end", "joining", "joined", "created", "updated")
    for col in list(df.columns):
        col_lower = str(col).lower()
        if not any(k in col_lower for k in date_keywords):
            continue
        if df[col].dtype != object:
            continue
        parsed = parse_date_series(df[col])
        success_ratio = float(parsed.notna().sum() / max(df[col].notna().sum(), 1))
        if success_ratio >= 0.5:
            failures = int(((parsed.isna()) & (df[col].notna())).sum())
            df[col] = parsed
            if failures:
                conversion_errors[str(col)] = conversion_errors.get(str(col), 0) + failures
                log.append(f"{failures} dates invalides dans '{col}'")

    return {
        "dataframe": df,
        "conversion_errors": conversion_errors,
        "removed_empty_columns": removed_empty,
        "removed_constant_columns": constants,
        "duplicate_rows_removed": duplicates_removed,
        "log": log,
    }
