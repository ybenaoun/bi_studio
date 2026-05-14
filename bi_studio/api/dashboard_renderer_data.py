"""Compute the actual numeric values for KPIs and chart data from the cleaned
dataframe. Cohere never produces values — only structure.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from bi_studio.utils.json_schema import ALLOWED_AGGREGATIONS


def _format_value(value: Any, fmt: str | None) -> Any:
    """Best-effort numeric formatting hint. The frontend handles display."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return round(float(value), 4)
    return value


def calculate_kpi(df: pd.DataFrame, kpi_spec: dict[str, Any]) -> dict[str, Any]:
    """Compute a dashboard.v1 KPI value from the cleaned dataframe."""
    data = kpi_spec.get("data") or {}
    aggregation = str(data.get("aggregation") or "").lower()
    field = data.get("metric")
    fmt = kpi_spec.get("format") or {"type": "number", "decimals": 2}

    if aggregation not in ALLOWED_AGGREGATIONS:
        return {"value": None, "error": f"aggregation invalide: {aggregation}"}

    if aggregation == "count":
        if field and field in df.columns:
            return {"value": int(df[field].count()), "format": fmt}
        return {"value": int(len(df.index)), "format": fmt}

    if not field or field not in df.columns:
        return {"value": None, "error": f"colonne inexistante: {field}"}

    series = df[field].dropna()
    if series.empty:
        return {"value": 0, "format": fmt}

    value: Any = None
    if aggregation == "sum":
        value = float(pd.to_numeric(series, errors="coerce").dropna().sum())
    elif aggregation == "avg":
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        value = float(numeric.mean()) if not numeric.empty else None
    elif aggregation == "min":
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        value = float(numeric.min()) if not numeric.empty else None
    elif aggregation == "max":
        numeric = pd.to_numeric(series, errors="coerce").dropna()
        value = float(numeric.max()) if not numeric.empty else None

    return {"value": _format_value(value, fmt.get("type") if isinstance(fmt, dict) else None), "format": fmt}


def _aggregate_series(series: pd.Series, aggregation: str) -> Any:
    aggregation = (aggregation or "sum").lower()
    if aggregation not in ALLOWED_AGGREGATIONS:
        aggregation = "sum"
    if aggregation == "count":
        return int(series.size)
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return None
    if aggregation == "sum":
        return float(numeric.sum())
    if aggregation == "avg":
        return float(numeric.mean())
    if aggregation == "min":
        return float(numeric.min())
    if aggregation == "max":
        return float(numeric.max())
    return None


def calculate_chart_data(df: pd.DataFrame, chart_spec: dict[str, Any]) -> dict[str, Any]:
    """Compute the data points for a dashboard.v1 chart spec."""
    widget_type = str(chart_spec.get("type") or "").lower()
    data = chart_spec.get("data") or {}
    aggregation = str(data.get("aggregation") or "sum").lower()
    x_axis = data.get("x")
    y_axis = data.get("y")
    category = data.get("category")
    value = data.get("value")
    top_n = int(data.get("limit") or 25)

    if widget_type == "bar_chart":
        if not x_axis or x_axis not in df.columns:
            return {"labels": [], "values": [], "error": "groupement invalide"}
        if aggregation == "count":
            grouped = df.groupby(x_axis, dropna=False).size()
        elif y_axis and y_axis in df.columns:
            grouped = df.groupby(x_axis, dropna=False)[y_axis].apply(
                lambda s: _aggregate_series(s, aggregation)
            )
        else:
            return {"labels": [], "values": [], "error": "mesure invalide"}
        grouped = grouped.dropna().sort_values(ascending=False).head(top_n)
        return {
            "labels": [str(idx) if idx is not None else "(vide)" for idx in grouped.index],
            "values": [float(v) if v is not None else 0 for v in grouped.values],
        }

    if widget_type == "line_chart":
        if not x_axis or x_axis not in df.columns:
            return {"labels": [], "values": [], "error": "axe X invalide"}
        date_series = pd.to_datetime(df[x_axis], errors="coerce")
        if date_series.notna().any():
            valid = df[date_series.notna()].copy()
            valid["__period__"] = pd.to_datetime(valid[x_axis], errors="coerce").dt.to_period("M")
            group_key = "__period__"
        else:
            valid = df.copy()
            group_key = x_axis
        if aggregation == "count":
            grouped = valid.groupby(group_key, dropna=False).size()
        elif y_axis and y_axis in df.columns:
            grouped = valid.groupby(group_key, dropna=False)[y_axis].apply(
                lambda s: _aggregate_series(s, aggregation)
            )
        else:
            return {"labels": [], "values": [], "error": "mesure invalide"}
        grouped = grouped.dropna().sort_index()
        return {
            "labels": [str(p) for p in grouped.index],
            "values": [float(v) for v in grouped.values],
        }

    if widget_type == "pie_chart":
        if not category or category not in df.columns:
            return {"labels": [], "values": [], "error": "groupement invalide"}
        if aggregation == "count":
            grouped = df.groupby(category, dropna=False).size()
        elif value and value in df.columns:
            grouped = df.groupby(category, dropna=False)[value].apply(
                lambda s: _aggregate_series(s, aggregation)
            )
        else:
            return {"labels": [], "values": [], "error": "mesure invalide"}
        grouped = grouped.dropna().sort_values(ascending=False).head(top_n)
        return {
            "labels": [str(idx) if idx is not None else "(vide)" for idx in grouped.index],
            "values": [float(v) if v is not None else 0 for v in grouped.values],
        }

    return {"error": f"Type de graphique non supporté: {widget_type}"}


def calculate_table_data(df: pd.DataFrame, table_spec: dict[str, Any]) -> dict[str, Any]:
    data = table_spec.get("data") or {}
    limit = int(data.get("limit") or 100)
    columns = [c for c in (data.get("columns") or []) if c in df.columns]
    if not columns:
        columns = list(df.columns)
    sub = df[columns].head(limit).where(pd.notnull(df[columns].head(limit)), None)
    return {
        "columns": columns,
        "rows": sub.to_dict(orient="records"),
    }


def calculate_filter_data(df: pd.DataFrame, filter_spec: dict[str, Any]) -> dict[str, Any]:
    data = filter_spec.get("data") or {}
    metric = data.get("metric")
    if not metric or metric not in df.columns:
        return {"values": [], "error": f"colonne inexistante: {metric}"}
    values = (
        df[metric]
        .dropna()
        .astype(str)
        .value_counts()
        .head(100)
        .index
        .tolist()
    )
    return {"field": metric, "values": values}
