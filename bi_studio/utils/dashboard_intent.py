"""Build validated dashboard.v1 definitions from a small AI intent.

Cohere is intentionally kept away from the final renderer contract. It may only
suggest intent fields; this module chooses safe columns and creates the strict
dashboard.v1 object that the backend validates before rendering.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

from bi_studio.utils.json_schema import (
    ALLOWED_WIDGET_TYPES,
    DASHBOARD_SCHEMA_VERSION,
    LAYOUT_COLUMNS,
    LAYOUT_ROW_HEIGHT,
)

INTENT_KEYS = {
    "title",
    "preferred_widgets",
    "main_metric",
    "main_dimension",
    "date_column",
    "filters",
    "table_columns",
}


def parse_dashboard_intent(raw_text: str) -> dict[str, Any]:
    """Parse Cohere's small intent JSON. Invalid intent simply means fallback."""
    text = (raw_text or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except Exception:
        return {}
    if isinstance(payload, dict) and set(payload).issubset({"error", "raw"}) and isinstance(payload.get("raw"), str):
        return parse_dashboard_intent(payload["raw"])
    if not isinstance(payload, dict):
        return {}
    return {str(key): value for key, value in payload.items() if key in INTENT_KEYS}


def build_dashboard_definition_from_intent(
    intent: dict[str, Any] | None,
    dataset_schema: dict[str, Any],
    *,
    fallback_title: str = "Dashboard",
) -> dict[str, Any]:
    """Create the final dashboard.v1 definition from AI intent + dataset schema."""
    columns = _column_index(dataset_schema)
    normalized = normalize_dashboard_intent(intent or {}, columns, fallback_title=fallback_title)

    widgets: list[dict[str, Any]] = []
    used_ids: set[str] = set()

    def add_widget(widget: dict[str, Any]) -> None:
        widget["id"] = _unique_id(widget["id"], used_ids)
        used_ids.add(widget["id"])
        widgets.append(widget)

    first_column = _first_column(columns)
    metric = normalized.get("main_metric")
    dimension = normalized.get("main_dimension")
    date_column = normalized.get("date_column")
    preferred_widgets = set(normalized.get("preferred_widgets") or [])
    table_columns = normalized.get("table_columns") or list(columns)[:8]

    filter_y = 0
    top_y = 0
    if "filter" in preferred_widgets and dimension:
        add_widget({
            "id": f"{dimension}_filter",
            "type": "filter",
            "title": f"Filtre {_label(columns[dimension])}",
            "position": {"x": 0, "y": filter_y, "w": 3, "h": 1},
            "data": {"source": "main", "metric": dimension},
        })
        top_y = 1

    if "kpi_card" in preferred_widgets and (metric or first_column):
        if metric:
            format_spec = _format_for_metric(metric, columns[metric])
            add_widget({
                "id": f"total_{metric}",
                "type": "kpi_card",
                "title": f"Total {_label(columns[metric])}",
                "position": {"x": 0, "y": top_y, "w": 3, "h": 2},
                "data": {"source": "main", "metric": metric, "aggregation": "sum"},
                "format": format_spec,
            })
        else:
            add_widget({
                "id": "row_count",
                "type": "kpi_card",
                "title": "Nombre de lignes",
                "position": {"x": 0, "y": top_y, "w": 3, "h": 2},
                "data": {"source": "main", "metric": first_column, "aggregation": "count"},
                "format": {"type": "number", "decimals": 0},
            })

    chart_y = top_y + (2 if any(w["type"] == "kpi_card" for w in widgets) else 0)
    chart_slots = 0
    if "bar_chart" in preferred_widgets and dimension and metric:
        add_widget({
            "id": f"{metric}_by_{dimension}",
            "type": "bar_chart",
            "title": f"{_label(columns[metric])} par {_label(columns[dimension])}",
            "position": {"x": 0, "y": chart_y, "w": 6, "h": 4},
            "data": {"source": "main", "x": dimension, "y": metric, "aggregation": "sum"},
            "options": {"orientation": "vertical", "show_legend": True, "stacked": False},
        })
        chart_slots += 1

    if "line_chart" in preferred_widgets and date_column and metric:
        add_widget({
            "id": f"{metric}_trend",
            "type": "line_chart",
            "title": f"Évolution {_label(columns[metric])}",
            "position": {"x": 6 if chart_slots % 2 else 0, "y": chart_y + (chart_slots // 2) * 4, "w": 6, "h": 4},
            "data": {"source": "main", "x": date_column, "y": metric, "aggregation": "sum"},
            "options": {"show_legend": True},
        })
        chart_slots += 1

    if "pie_chart" in preferred_widgets and dimension and metric:
        add_widget({
            "id": f"{metric}_share_by_{dimension}",
            "type": "pie_chart",
            "title": f"Répartition {_label(columns[metric])} par {_label(columns[dimension])}",
            "position": {"x": 0, "y": chart_y + ((chart_slots + 1) // 2) * 4, "w": 4, "h": 4},
            "data": {"source": "main", "category": dimension, "value": metric, "aggregation": "sum"},
            "options": {"show_legend": True},
        })
        chart_slots += 1

    max_y = max((int(w["position"]["y"]) + int(w["position"]["h"]) for w in widgets), default=0)
    if ("data_table" in preferred_widgets or not widgets) and table_columns:
        add_widget({
            "id": "details_table",
            "type": "data_table",
            "title": "Détails",
            "position": {"x": 0, "y": max_y, "w": 12, "h": 5},
            "data": {"source": "main", "columns": table_columns, "limit": 100},
        })

    return {
        "schema_version": DASHBOARD_SCHEMA_VERSION,
        "title": normalized["title"],
        "layout": {"columns": LAYOUT_COLUMNS, "row_height": LAYOUT_ROW_HEIGHT},
        "widgets": widgets,
    }


def normalize_dashboard_intent(
    intent: dict[str, Any],
    columns: dict[str, dict[str, Any]],
    *,
    fallback_title: str,
) -> dict[str, Any]:
    lookup = _column_lookup(columns)
    preferred = [
        widget
        for widget in _as_string_list(intent.get("preferred_widgets"))
        if widget in ALLOWED_WIDGET_TYPES
    ]
    if not preferred:
        preferred = ["kpi_card", "bar_chart", "line_chart", "pie_chart", "data_table"]

    main_metric = _resolve_column(intent.get("main_metric"), columns, lookup)
    if not main_metric or not _is_numeric(columns.get(main_metric, {})):
        main_metric = _first_numeric(columns)

    main_dimension = _resolve_column(intent.get("main_dimension"), columns, lookup)
    if not main_dimension or not _is_categorical(columns.get(main_dimension, {})):
        main_dimension = _first_categorical(columns)

    date_column = _resolve_column(intent.get("date_column"), columns, lookup)
    if not date_column or not _is_date(columns.get(date_column, {})):
        date_column = _first_date(columns)

    table_columns = []
    for column in _as_string_list(intent.get("table_columns")):
        resolved = _resolve_column(column, columns, lookup)
        if resolved and resolved not in table_columns:
            table_columns.append(resolved)
    if not table_columns:
        table_columns = _default_table_columns(columns, main_metric, main_dimension, date_column)

    return {
        "title": _clean_title(intent.get("title") or fallback_title),
        "preferred_widgets": preferred,
        "main_metric": main_metric,
        "main_dimension": main_dimension,
        "date_column": date_column,
        "filters": [
            col
            for col in (_resolve_column(value, columns, lookup) for value in _as_string_list(intent.get("filters")))
            if col and col in columns
        ],
        "table_columns": table_columns,
    }


def _column_index(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    columns = schema.get("columns") if isinstance(schema, dict) else None
    if not isinstance(columns, list):
        return {}
    index: dict[str, dict[str, Any]] = {}
    for column in columns:
        if isinstance(column, dict) and column.get("name"):
            index[str(column["name"])] = column
    return index


def _column_lookup(columns: dict[str, dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for name, column in columns.items():
        for value in (name, column.get("label"), column.get("original_name")):
            key = _lookup_key(value)
            if key:
                lookup[key] = name
    return lookup


def _resolve_column(value: Any, columns: dict[str, dict[str, Any]], lookup: dict[str, str]) -> str | None:
    if not value:
        return None
    text = str(value)
    if text in columns:
        return text
    return lookup.get(_lookup_key(text))


def _lookup_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _as_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return []


def _clean_title(value: Any) -> str:
    title = str(value or "Dashboard").strip()
    return title[:120] or "Dashboard"


def _semantic(column: dict[str, Any]) -> str:
    return str(column.get("semantic_type") or column.get("semantic_role") or "").lower().strip()


def _detected_type(column: dict[str, Any]) -> str:
    return str(column.get("type") or column.get("detected_type") or "").lower().strip()


def _is_numeric(column: dict[str, Any]) -> bool:
    return _semantic(column) == "measure" or _detected_type(column) in {"number", "currency"}


def _is_date(column: dict[str, Any]) -> bool:
    return _semantic(column) in {"date", "datetime"} or _detected_type(column) in {"date", "datetime"}


def _is_categorical(column: dict[str, Any]) -> bool:
    return _semantic(column) in {"dimension", "attribute", "identifier"} or _detected_type(column) in {
        "category",
        "text",
        "identifier",
        "boolean",
    }


def _first_column(columns: dict[str, dict[str, Any]]) -> str | None:
    return next(iter(columns), None)


def _first_numeric(columns: dict[str, dict[str, Any]]) -> str | None:
    for name, column in columns.items():
        if _is_numeric(column):
            return name
    return None


def _first_categorical(columns: dict[str, dict[str, Any]]) -> str | None:
    for name, column in columns.items():
        if _is_categorical(column):
            return name
    return None


def _first_date(columns: dict[str, dict[str, Any]]) -> str | None:
    for name, column in columns.items():
        if _is_date(column):
            return name
    return None


def _default_table_columns(
    columns: dict[str, dict[str, Any]],
    main_metric: str | None,
    main_dimension: str | None,
    date_column: str | None,
) -> list[str]:
    ordered = [col for col in (date_column, main_dimension, main_metric) if col]
    for name in columns:
        if name not in ordered:
            ordered.append(name)
    return ordered[:8]


def _label(column: dict[str, Any]) -> str:
    return str(column.get("label") or column.get("name") or "").replace("_", " ").strip()


def _format_for_metric(name: str, column: dict[str, Any]) -> dict[str, Any]:
    text = f"{name} {_label(column)}".lower()
    if _detected_type(column) == "currency" or any(marker in text for marker in ("revenue", "salary", "amount", "montant", "prix", "ca")):
        return {"type": "currency", "currency": "EUR", "decimals": 2}
    return {"type": "number", "decimals": 2}


def _unique_id(raw: Any, used: set[str]) -> str:
    base = _snake_id(raw)
    candidate = base
    suffix = 2
    while candidate in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def _snake_id(value: Any) -> str:
    text = _lookup_key(value).replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    if not text or not re.match(r"^[a-z]", text):
        text = f"widget_{text}" if text else "widget"
    return text
