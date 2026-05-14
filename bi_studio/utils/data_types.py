import re
from dataclasses import asdict, dataclass

import pandas as pd

from bi_studio.services.naming import dedupe_names, scrub_label


MISSING_STRINGS = {"", "nan", "none", "null", "n/a", "na", "-", "--"}
IDENTIFIER_PATTERN = re.compile(r"(^|_|\b)(id|code|ref|reference|matricule|uuid|key|no|number)(_|$|\b)")
DATE_PATTERN = re.compile(r"(^|_|\b)(date|start|end|joining|joined|created|updated|period|month|year|time)(_|$|\b)")
MEASURE_PATTERN = re.compile(
    r"(amount|salary|total|price|cost|quantity|qty|revenue|sales|balance|budget|fee|payment|debit|credit|rate|score)"
)
CURRENCY_PATTERN = re.compile(r"[$€£¥₦]|usd|eur|gbp|ngn|xaf|xof", re.IGNORECASE)
TRUE_VALUES = {"true", "yes", "y", "1", "oui", "vrai"}
FALSE_VALUES = {"false", "no", "n", "0", "non", "faux"}


@dataclass
class SemanticProfile:
    original_column_name: str
    normalized_column_name: str
    detected_type: str
    semantic_role: str
    null_count: int
    null_rate: float
    unique_count: int
    sample_values: list
    min_value: str | None = None
    max_value: str | None = None
    mean_value: float | None = None
    confidence: float = 0
    warnings: list | None = None
    aggregation: str | None = None
    include: bool = True

    @property
    def is_numeric(self):
        return self.detected_type in {"Number", "Currency"} and self.semantic_role == "Measure"

    @property
    def is_date(self):
        return self.detected_type == "Date" or self.semantic_role == "Date Dimension"

    @property
    def is_category(self):
        return self.semantic_role in {"Dimension", "Date Dimension"}

    def as_dict(self):
        value = asdict(self)
        value["warnings"] = self.warnings or []
        value["is_numeric"] = self.is_numeric
        value["is_date"] = self.is_date
        value["is_category"] = self.is_category
        return value


def normalise_missing(value):
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in MISSING_STRINGS:
            return None
        return stripped
    return value


def clean_series(series):
    return series.map(normalise_missing)


def normalize_dataframe_columns(raw_df):
    df = raw_df.copy()
    labels = [str(column or "") for column in df.columns]
    df.columns = dedupe_names(labels)
    df = df.dropna(how="all").reset_index(drop=True)
    return df, labels


def dataframe_preview(df, limit=50):
    preview_df = df.head(limit).copy()
    preview_df = preview_df.where(pd.notnull(preview_df), None)
    return preview_df.to_dict(orient="records")


def parse_number_series(series):
    def clean(value):
        value = normalise_missing(value)
        if value is None:
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return value
        text = str(value).strip()
        if not text:
            return None
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        text = re.sub(r"[$€£¥₦]", "", text)
        text = re.sub(r"\b(usd|eur|gbp|ngn|xaf|xof)\b", "", text, flags=re.IGNORECASE)
        text = text.replace(" ", "").replace(",", "")
        text = text.replace("%", "")
        if negative:
            text = f"-{text}"
        return text

    return pd.to_numeric(series.map(clean), errors="coerce")


def parse_date_series(series):
    return pd.to_datetime(series.map(normalise_missing), errors="coerce")


def parse_boolean_series(series):
    def clean(value):
        value = normalise_missing(value)
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in TRUE_VALUES:
            return True
        if text in FALSE_VALUES:
            return False
        return None

    return series.map(clean)


def looks_like_date_values(series):
    non_null = clean_series(series).dropna().head(20)
    if non_null.empty:
        return False
    if any(hasattr(value, "year") and hasattr(value, "month") for value in non_null):
        return True
    text_values = non_null.astype(str)
    return bool(text_values.str.contains(r"[-/]|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec", case=False).mean() >= 0.5)


def infer_semantic_role(name, detected_type, unique_ratio, distinct_count, row_count, avg_text_length, warnings):
    if "empty_column" in warnings or "constant_column" in warnings:
        return "Ignored"
    if detected_type == "Identifier":
        return "Identifier"
    if detected_type == "Date":
        return "Date Dimension"
    if detected_type in {"Number", "Currency"}:
        if IDENTIFIER_PATTERN.search(name) and unique_ratio >= 0.5:
            return "Identifier"
        return "Measure"
    if detected_type in {"Boolean", "Category"}:
        return "Dimension"
    if detected_type == "Text" and distinct_count <= max(3, min(50, row_count * 0.35)) and avg_text_length <= 80:
        return "Dimension"
    if detected_type == "Unknown":
        return "Ignored"
    return "Attribute"


def default_aggregation(detected_type, semantic_role, name):
    if semantic_role == "Measure":
        if detected_type == "Currency" or MEASURE_PATTERN.search(name):
            return "sum"
        return "average"
    if semantic_role == "Identifier":
        return "count_distinct"
    if semantic_role == "Dimension":
        return "count"
    if semantic_role == "Date Dimension":
        return "range"
    return None


def detect_column_profile(series, original_name, normalized_name, row_count):
    cleaned = clean_series(series)
    non_null_count = int(cleaned.notna().sum())
    null_count = int(cleaned.isna().sum())
    null_rate = round(null_count / row_count, 4) if row_count else 0
    unique_count = int(cleaned.nunique(dropna=True))
    unique_ratio = unique_count / non_null_count if non_null_count else 0
    samples = [str(value) for value in cleaned.dropna().head(5).tolist()]
    warnings = []
    name_blob = f"{original_name} {normalized_name} {scrub_label(original_name)}".lower()

    if non_null_count == 0:
        warnings.append("empty_column")
    if non_null_count > 0 and unique_count <= 1:
        warnings.append("constant_column")

    numeric = parse_number_series(cleaned)
    numeric_ratio = float(numeric.notna().sum() / non_null_count) if non_null_count else 0
    should_check_date = DATE_PATTERN.search(name_blob) is not None or looks_like_date_values(cleaned)
    dates = parse_date_series(cleaned) if should_check_date else pd.Series([pd.NaT] * len(cleaned), index=cleaned.index)
    date_ratio = float(dates.notna().sum() / non_null_count) if non_null_count else 0
    booleans = parse_boolean_series(cleaned)
    bool_ratio = float(booleans.notna().sum() / non_null_count) if non_null_count else 0
    text_lengths = cleaned.dropna().astype(str).map(len)
    avg_text_length = float(text_lengths.mean()) if not text_lengths.empty else 0

    has_identifier_name = IDENTIFIER_PATTERN.search(name_blob) is not None
    has_measure_name = MEASURE_PATTERN.search(name_blob) is not None
    has_currency_values = bool(cleaned.dropna().astype(str).str.contains(CURRENCY_PATTERN).any()) if non_null_count else False
    detected_type = "Unknown"
    confidence = 0.0

    if non_null_count == 0:
        detected_type = "Unknown"
        confidence = 1.0
    elif has_identifier_name and unique_ratio >= 0.65:
        detected_type = "Identifier"
        confidence = min(0.98, 0.7 + unique_ratio * 0.25)
    elif should_check_date and date_ratio >= 0.5:
        detected_type = "Date"
        confidence = date_ratio
    elif bool_ratio >= 0.9 and unique_count <= 3:
        detected_type = "Boolean"
        confidence = bool_ratio
    elif numeric_ratio >= 0.75 or (has_measure_name and numeric_ratio >= 0.5):
        detected_type = "Currency" if has_currency_values or has_measure_name else "Number"
        confidence = numeric_ratio
    elif unique_count <= max(3, min(50, row_count * 0.35)) and avg_text_length <= 80:
        detected_type = "Category"
        confidence = max(0.65, 1 - unique_ratio)
    else:
        detected_type = "Text"
        confidence = 0.72 if avg_text_length <= 120 else 0.85

    if detected_type in {"Number", "Currency"} and 0 < numeric_ratio < 1:
        warnings.append("partial_number_conversion")
    if detected_type == "Date" and 0 < date_ratio < 1:
        warnings.append("partial_date_conversion")
    if detected_type == "Text" and avg_text_length > 120:
        warnings.append("long_text")

    semantic_role = infer_semantic_role(
        name_blob,
        detected_type,
        unique_ratio,
        unique_count,
        row_count,
        avg_text_length,
        warnings,
    )
    if normalized_name in {"name", "full_name", "employee_name", "customer_name"} and unique_ratio >= 0.5:
        semantic_role = "Attribute"
    include = semantic_role != "Ignored"
    aggregation = default_aggregation(detected_type, semantic_role, name_blob)

    min_value = None
    max_value = None
    mean_value = None
    if detected_type in {"Number", "Currency"} and semantic_role == "Measure":
        valid = numeric.dropna()
        if not valid.empty:
            min_value = str(valid.min())
            max_value = str(valid.max())
            mean_value = round(float(valid.mean()), 4)
    elif detected_type == "Date":
        valid = dates.dropna()
        if not valid.empty:
            min_value = valid.min().date().isoformat()
            max_value = valid.max().date().isoformat()

    return SemanticProfile(
        original_column_name=str(original_name),
        normalized_column_name=normalized_name,
        detected_type=detected_type,
        semantic_role=semantic_role,
        null_count=null_count,
        null_rate=null_rate,
        unique_count=unique_count,
        sample_values=samples,
        min_value=min_value,
        max_value=max_value,
        mean_value=mean_value,
        confidence=round(float(confidence), 4),
        warnings=warnings,
        aggregation=aggregation,
        include=include,
    )


def profile_dataframe(raw_df):
    df, original_labels = normalize_dataframe_columns(raw_df)
    row_count = len(df.index)
    profiles = []
    for index, column in enumerate(df.columns):
        original = original_labels[index] if index < len(original_labels) else column
        profiles.append(detect_column_profile(df[column], original, column, row_count))
    return df, profiles


def mapping_from_profiles(profiles):
    return {
        "columns": [
            {
                "original_name": profile.original_column_name,
                "normalized_name": profile.normalized_column_name,
                "detected_type": profile.detected_type,
                "semantic_role": profile.semantic_role,
                "aggregation": profile.aggregation,
                "include": bool(profile.include),
            }
            for profile in profiles
        ]
    }


def normalize_category_value(value):
    value = normalise_missing(value)
    if value is None:
        return None
    text = str(value).strip()
    lowered = text.lower()
    if lowered in {"m", "male", "man", "masculin", "homme"}:
        return "Male"
    if lowered in {"f", "female", "woman", "feminin", "feminine", "femme"}:
        return "Female"
    return re.sub(r"\s+", " ", text).strip().title()
