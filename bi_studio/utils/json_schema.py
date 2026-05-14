"""Strict dashboard.v1 contract and validators for AI-generated dashboards.

The renderer only accepts a validated ``dashboard.v1`` definition. Cohere may
produce aliases for common keys, so aliases are normalized first, then strict
schema and business validation reject anything the dashboard engine does not
understand.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


DASHBOARD_SCHEMA_VERSION = "dashboard.v1"
LAYOUT_COLUMNS = 12
LAYOUT_ROW_HEIGHT = 80

ALLOWED_WIDGET_TYPES = {
    "kpi_card",
    "bar_chart",
    "line_chart",
    "pie_chart",
    "data_table",
    "filter",
}
ALLOWED_AGGREGATIONS = {"sum", "avg", "min", "max", "count"}
ALLOWED_FORMAT_TYPES = {"number", "currency", "percentage"}
ALLOWED_ORIENTATIONS = {"vertical", "horizontal"}

ROOT_KEYS = {"schema_version", "title", "layout", "widgets"}
LAYOUT_KEYS = {"columns", "row_height"}
WIDGET_KEYS = {"id", "type", "title", "position", "data", "options", "format"}
POSITION_KEYS = {"x", "y", "w", "h"}
DATA_KEYS = {"source", "metric", "x", "y", "category", "value", "columns", "aggregation", "limit"}
OPTIONS_KEYS = {"show_legend", "orientation", "stacked"}
FORMAT_KEYS = {"type", "currency", "decimals"}

WIDGET_REQUIRED: dict[str, set[str]] = {
    "kpi_card": {"id", "type", "title", "position", "data", "format"},
    "bar_chart": {"id", "type", "title", "position", "data", "options"},
    "line_chart": {"id", "type", "title", "position", "data", "options"},
    "pie_chart": {"id", "type", "title", "position", "data", "options"},
    "data_table": {"id", "type", "title", "position", "data"},
    "filter": {"id", "type", "title", "position", "data"},
}

WIDGET_DATA_REQUIRED: dict[str, set[str]] = {
    "kpi_card": {"source", "metric", "aggregation"},
    "bar_chart": {"source", "x", "y", "aggregation"},
    "line_chart": {"source", "x", "y", "aggregation"},
    "pie_chart": {"source", "category", "value", "aggregation"},
    "data_table": {"source", "columns", "limit"},
    "filter": {"source", "metric"},
}

WIDGET_DATA_ALLOWED: dict[str, set[str]] = {
    "kpi_card": {"source", "metric", "aggregation"},
    "bar_chart": {"source", "x", "y", "aggregation"},
    "line_chart": {"source", "x", "y", "aggregation"},
    "pie_chart": {"source", "category", "value", "aggregation"},
    "data_table": {"source", "columns", "limit"},
    "filter": {"source", "metric"},
}

WIDGET_OPTIONS_REQUIRED: dict[str, set[str]] = {
    "bar_chart": {"orientation", "show_legend", "stacked"},
    "line_chart": {"show_legend"},
    "pie_chart": {"show_legend"},
}

WIDGET_OPTIONS_ALLOWED: dict[str, set[str]] = {
    "bar_chart": {"orientation", "show_legend", "stacked"},
    "line_chart": {"show_legend"},
    "pie_chart": {"show_legend"},
}

WIDGET_FORMAT_REQUIRED: dict[str, set[str]] = {
    "kpi_card": {"type", "decimals"},
}

WIDGET_FORMAT_ALLOWED: dict[str, set[str]] = {
    "kpi_card": {"type", "currency", "decimals"},
}

ALIAS_KEYS = {
    "graphTitle": "title",
    "chartTitle": "title",
    "titre": "title",
    "dataset": "source",
    "dataSource": "source",
    "source_name": "source",
    "xAxis": "x",
    "yAxis": "y",
    "labelX": "x",
    "labelY": "y",
    "measure": "metric",
    "metric_name": "metric",
    "dimension": "category",
    "legend": "show_legend",
    "showLegend": "show_legend",
    "showLegends": "show_legend",
    "isStacked": "stacked",
}


DashboardDefinition = dict[str, Any]
DashboardLayout = dict[str, Any]
DashboardWidget = dict[str, Any]
KpiCardWidget = dict[str, Any]
BarChartWidget = dict[str, Any]
LineChartWidget = dict[str, Any]
PieChartWidget = dict[str, Any]
DataTableWidget = dict[str, Any]
FilterWidget = dict[str, Any]


@dataclass(frozen=True)
class DashboardValidationError:
    path: str
    message: str


class DashboardJsonParseError(ValueError):
    """Raised when the model output is not a raw JSON object."""


class DashboardValidationException(ValueError):
    """Raised when a dashboard.v1 definition remains invalid after repair."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


DASHBOARD_V1_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "dashboard.v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "title", "layout", "widgets"],
    "properties": {
        "schema_version": {"const": DASHBOARD_SCHEMA_VERSION},
        "title": {"type": "string"},
        "layout": {
            "type": "object",
            "additionalProperties": False,
            "required": ["columns", "row_height"],
            "properties": {
                "columns": {"const": LAYOUT_COLUMNS},
                "row_height": {"const": LAYOUT_ROW_HEIGHT},
            },
        },
        "widgets": {
            "type": "array",
            "items": {
                "oneOf": [
                    {"$ref": "#/$defs/kpi_card"},
                    {"$ref": "#/$defs/bar_chart"},
                    {"$ref": "#/$defs/line_chart"},
                    {"$ref": "#/$defs/pie_chart"},
                    {"$ref": "#/$defs/data_table"},
                    {"$ref": "#/$defs/filter"},
                ]
            },
        },
    },
    "$defs": {
        "position": {
            "type": "object",
            "additionalProperties": False,
            "required": ["x", "y", "w", "h"],
            "properties": {
                "x": {"type": "integer", "minimum": 0, "maximum": 11},
                "y": {"type": "integer", "minimum": 0},
                "w": {"type": "integer", "minimum": 1, "maximum": 12},
                "h": {"type": "integer", "minimum": 1},
            },
        },
        "format": {
            "type": "object",
            "additionalProperties": False,
            "required": ["type", "decimals"],
            "properties": {
                "type": {"enum": sorted(ALLOWED_FORMAT_TYPES)},
                "currency": {"type": "string"},
                "decimals": {"type": "integer", "minimum": 0},
            },
        },
        "kpi_card": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "type", "title", "position", "data", "format"],
            "properties": {
                "id": {"type": "string"},
                "type": {"const": "kpi_card"},
                "title": {"type": "string"},
                "position": {"$ref": "#/$defs/position"},
                "data": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "metric", "aggregation"],
                    "properties": {
                        "source": {"type": "string"},
                        "metric": {"type": "string"},
                        "aggregation": {"enum": sorted(ALLOWED_AGGREGATIONS)},
                    },
                },
                "format": {"$ref": "#/$defs/format"},
            },
        },
        "bar_chart": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "type", "title", "position", "data", "options"],
            "properties": {
                "id": {"type": "string"},
                "type": {"const": "bar_chart"},
                "title": {"type": "string"},
                "position": {"$ref": "#/$defs/position"},
                "data": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "x", "y", "aggregation"],
                    "properties": {
                        "source": {"type": "string"},
                        "x": {"type": "string"},
                        "y": {"type": "string"},
                        "aggregation": {"enum": sorted(ALLOWED_AGGREGATIONS)},
                    },
                },
                "options": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["orientation", "show_legend", "stacked"],
                    "properties": {
                        "orientation": {"enum": sorted(ALLOWED_ORIENTATIONS)},
                        "show_legend": {"type": "boolean"},
                        "stacked": {"type": "boolean"},
                    },
                },
            },
        },
        "line_chart": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "type", "title", "position", "data", "options"],
            "properties": {
                "id": {"type": "string"},
                "type": {"const": "line_chart"},
                "title": {"type": "string"},
                "position": {"$ref": "#/$defs/position"},
                "data": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "x", "y", "aggregation"],
                    "properties": {
                        "source": {"type": "string"},
                        "x": {"type": "string"},
                        "y": {"type": "string"},
                        "aggregation": {"enum": sorted(ALLOWED_AGGREGATIONS)},
                    },
                },
                "options": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["show_legend"],
                    "properties": {"show_legend": {"type": "boolean"}},
                },
            },
        },
        "pie_chart": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "type", "title", "position", "data", "options"],
            "properties": {
                "id": {"type": "string"},
                "type": {"const": "pie_chart"},
                "title": {"type": "string"},
                "position": {"$ref": "#/$defs/position"},
                "data": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "category", "value", "aggregation"],
                    "properties": {
                        "source": {"type": "string"},
                        "category": {"type": "string"},
                        "value": {"type": "string"},
                        "aggregation": {"enum": sorted(ALLOWED_AGGREGATIONS)},
                    },
                },
                "options": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["show_legend"],
                    "properties": {"show_legend": {"type": "boolean"}},
                },
            },
        },
        "data_table": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "type", "title", "position", "data"],
            "properties": {
                "id": {"type": "string"},
                "type": {"const": "data_table"},
                "title": {"type": "string"},
                "position": {"$ref": "#/$defs/position"},
                "data": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "columns", "limit"],
                    "properties": {
                        "source": {"type": "string"},
                        "columns": {"type": "array", "items": {"type": "string"}},
                        "limit": {"type": "integer", "minimum": 1},
                    },
                },
            },
        },
        "filter": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "type", "title", "position", "data"],
            "properties": {
                "id": {"type": "string"},
                "type": {"const": "filter"},
                "title": {"type": "string"},
                "position": {"$ref": "#/$defs/position"},
                "data": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["source", "metric"],
                    "properties": {
                        "source": {"type": "string"},
                        "metric": {"type": "string"},
                    },
                },
            },
        },
    },
}


def parse_dashboard_json(raw_text: str) -> DashboardDefinition:
    """Parse a raw Cohere response as a dashboard JSON object.

    The model answer must be directly parseable as JSON. If the shared AI
    gateway wraps a failed JSON parse as {"error": "...", "raw": "..."}, we
    unwrap only that technical envelope and validate the raw model text with
    the same strict rules.
    """
    text = (raw_text or "").strip()
    if not text:
        raise DashboardJsonParseError("Réponse IA vide: JSON dashboard.v1 attendu.")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DashboardJsonParseError(
            f"La réponse IA doit être un JSON brut directement parsable: {exc.msg} "
            f"(ligne {exc.lineno}, colonne {exc.colno})."
        )
    if not isinstance(payload, dict):
        raise DashboardJsonParseError("La réponse IA doit être un objet JSON dashboard.v1.")
    if "schema_version" not in payload and isinstance(payload.get("raw"), str):
        wrapper_keys = set(payload.keys())
        if wrapper_keys.issubset({"error", "raw"}):
            return parse_dashboard_json(payload["raw"])
    return payload


def normalize_dashboard_aliases(value: Any) -> Any:
    """Recursively normalize common Cohere key aliases before strict validation."""
    if isinstance(value, list):
        return [normalize_dashboard_aliases(item) for item in value]
    if not isinstance(value, dict):
        return value

    normalized: dict[str, Any] = {}
    for key, item in value.items():
        normalized_key = ALIAS_KEYS.get(str(key), str(key))
        normalized_value = normalize_dashboard_aliases(item)
        if normalized_key in normalized and normalized[normalized_key] != normalized_value:
            if normalized_key.startswith("__alias_conflict__"):
                normalized[normalized_key] = normalized_value
            else:
                normalized[f"__alias_conflict__{normalized_key}"] = normalized_value
            continue
        normalized[normalized_key] = normalized_value
    return normalized


def normalize_spec_columns(spec: DashboardDefinition, dataset_schema: dict[str, Any]) -> DashboardDefinition:
    """Resolve labels/original names to canonical dataset column names."""
    if not isinstance(spec, dict):
        return {}
    spec = normalize_dashboard_aliases(spec)
    columns = _column_index(dataset_schema)
    lookup = _column_lookup(columns)

    for widget in spec.get("widgets") or []:
        if not isinstance(widget, dict):
            continue
        data = widget.get("data")
        if not isinstance(data, dict):
            continue
        for field in ("metric", "x", "y", "category", "value"):
            resolved = _resolve_column(data.get(field), columns, lookup)
            if resolved:
                data[field] = resolved
        if isinstance(data.get("columns"), list):
            data["columns"] = [
                resolved or column
                for column in data.get("columns") or []
                for resolved in [_resolve_column(column, columns, lookup)]
            ]
    return spec


def prepare_dashboard_spec(spec: DashboardDefinition, dataset_schema: dict[str, Any]) -> DashboardDefinition:
    """Normalize aliases and column labels without adding fallback widgets."""
    if not isinstance(spec, dict):
        return {}
    normalized = normalize_dashboard_aliases(spec)
    return normalize_spec_columns(normalized, dataset_schema)


def validate_dashboard_response(raw_text: str, dataset_schema: dict[str, Any]) -> tuple[DashboardDefinition, list[str]]:
    """Parse, normalize and validate a raw Cohere response."""
    try:
        parsed = parse_dashboard_json(raw_text)
    except DashboardJsonParseError as exc:
        return {}, [str(exc)]
    normalized = prepare_dashboard_spec(parsed, dataset_schema)
    return normalized, validate_dashboard_spec(normalized, dataset_schema)


def validate_dashboard_spec(spec: DashboardDefinition, dataset_schema: dict[str, Any]) -> list[str]:
    """Validate a dashboard.v1 definition with strict schema and business rules."""
    if not isinstance(spec, dict):
        return ["La spécification doit être un objet JSON."]
    normalized = prepare_dashboard_spec(spec, dataset_schema)
    errors = _validate_schema(normalized)
    if errors:
        return errors
    return _validate_business_rules(normalized, dataset_schema)


def coerce_spec_defaults(spec: DashboardDefinition) -> DashboardDefinition:
    """Compatibility wrapper kept for older imports. It does not add widgets."""
    if not isinstance(spec, dict):
        return {}
    return normalize_dashboard_aliases(spec)


def repair_spec_for_execution(spec: DashboardDefinition, dataset_schema: dict[str, Any]) -> DashboardDefinition:
    """Compatibility wrapper: normalize only, then let strict validation decide."""
    if not isinstance(spec, dict):
        return {}
    return prepare_dashboard_spec(spec, dataset_schema)


def _validate_schema(spec: DashboardDefinition) -> list[str]:
    errors: list[str] = []
    errors.extend(_unknown_keys("$", spec, ROOT_KEYS))
    errors.extend(_require("$", spec, ROOT_KEYS))
    if spec.get("schema_version") != DASHBOARD_SCHEMA_VERSION:
        errors.append("schema_version doit être exactement 'dashboard.v1'.")
    if not isinstance(spec.get("title"), str) or not spec.get("title", "").strip():
        errors.append("title doit être une chaîne non vide.")

    layout = spec.get("layout")
    if not isinstance(layout, dict):
        errors.append("layout doit être un objet.")
    else:
        errors.extend(_unknown_keys("$.layout", layout, LAYOUT_KEYS))
        errors.extend(_require("$.layout", layout, LAYOUT_KEYS))
        if layout.get("columns") != LAYOUT_COLUMNS:
            errors.append("layout.columns doit être exactement 12.")
        if layout.get("row_height") != LAYOUT_ROW_HEIGHT:
            errors.append("layout.row_height doit être exactement 80.")

    widgets = spec.get("widgets")
    if not isinstance(widgets, list):
        errors.append("widgets doit être une liste.")
        return errors

    for index, widget in enumerate(widgets):
        path = f"$.widgets[{index}]"
        if not isinstance(widget, dict):
            errors.append(f"{path} doit être un objet.")
            continue
        errors.extend(_validate_widget_schema(path, widget))
    return errors


def _validate_widget_schema(path: str, widget: DashboardWidget) -> list[str]:
    errors: list[str] = []
    errors.extend(_unknown_keys(path, widget, WIDGET_KEYS))
    widget_type = widget.get("type")
    if widget_type not in ALLOWED_WIDGET_TYPES:
        errors.append(f"{path}.type widget inconnu '{widget_type}'.")
        return errors

    required = WIDGET_REQUIRED[widget_type]
    errors.extend(_require(path, widget, required))
    for field in ("id", "title", "type"):
        if field in widget and not isinstance(widget.get(field), str):
            errors.append(f"{path}.{field} doit être une chaîne.")

    position = widget.get("position")
    if not isinstance(position, dict):
        errors.append(f"{path}.position doit être un objet.")
    else:
        errors.extend(_unknown_keys(f"{path}.position", position, POSITION_KEYS))
        errors.extend(_require(f"{path}.position", position, POSITION_KEYS))
        for field in POSITION_KEYS:
            if field in position and not _is_integer(position.get(field)):
                errors.append(f"{path}.position.{field} doit être un entier.")
        if _is_integer(position.get("x")) and not 0 <= position["x"] <= 11:
            errors.append(f"{path}.position.x doit être entre 0 et 11.")
        if _is_integer(position.get("y")) and position["y"] < 0:
            errors.append(f"{path}.position.y doit être supérieur ou égal à 0.")
        if _is_integer(position.get("w")) and not 1 <= position["w"] <= 12:
            errors.append(f"{path}.position.w doit être entre 1 et 12.")
        if _is_integer(position.get("h")) and position["h"] < 1:
            errors.append(f"{path}.position.h doit être supérieur ou égal à 1.")

    data = widget.get("data")
    if not isinstance(data, dict):
        errors.append(f"{path}.data doit être un objet.")
    else:
        allowed_data = WIDGET_DATA_ALLOWED[widget_type]
        errors.extend(_unknown_keys(f"{path}.data", data, allowed_data))
        errors.extend(_require(f"{path}.data", data, WIDGET_DATA_REQUIRED[widget_type]))
        errors.extend(_validate_data_types(f"{path}.data", data))

    options = widget.get("options")
    if widget_type in WIDGET_OPTIONS_REQUIRED:
        if not isinstance(options, dict):
            errors.append(f"{path}.options doit être un objet.")
        else:
            allowed_options = WIDGET_OPTIONS_ALLOWED[widget_type]
            errors.extend(_unknown_keys(f"{path}.options", options, allowed_options))
            errors.extend(_require(f"{path}.options", options, WIDGET_OPTIONS_REQUIRED[widget_type]))
            errors.extend(_validate_options_types(f"{path}.options", options))
    elif options is not None:
        errors.append(f"{path}.options n'est pas prévu pour le widget {widget_type}.")

    fmt = widget.get("format")
    if widget_type in WIDGET_FORMAT_REQUIRED:
        if not isinstance(fmt, dict):
            errors.append(f"{path}.format doit être un objet.")
        else:
            allowed_format = WIDGET_FORMAT_ALLOWED[widget_type]
            errors.extend(_unknown_keys(f"{path}.format", fmt, allowed_format))
            errors.extend(_require(f"{path}.format", fmt, WIDGET_FORMAT_REQUIRED[widget_type]))
            errors.extend(_validate_format_types(f"{path}.format", fmt))
    elif fmt is not None:
        errors.append(f"{path}.format n'est pas prévu pour le widget {widget_type}.")

    return errors


def _validate_data_types(path: str, data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for field in ("source", "metric", "x", "y", "category", "value"):
        if field in data and not isinstance(data.get(field), str):
            errors.append(f"{path}.{field} doit être une chaîne.")
    if "aggregation" in data and data.get("aggregation") not in ALLOWED_AGGREGATIONS:
        errors.append(f"{path}.aggregation invalide '{data.get('aggregation')}'.")
    if "columns" in data:
        if not isinstance(data.get("columns"), list) or not all(isinstance(col, str) for col in data["columns"]):
            errors.append(f"{path}.columns doit être une liste de chaînes.")
    if "limit" in data and (not _is_integer(data.get("limit")) or int(data["limit"]) < 1):
        errors.append(f"{path}.limit doit être un entier positif.")
    return errors


def _validate_options_types(path: str, options: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "show_legend" in options and not isinstance(options.get("show_legend"), bool):
        errors.append(f"{path}.show_legend doit être un booléen.")
    if "stacked" in options and not isinstance(options.get("stacked"), bool):
        errors.append(f"{path}.stacked doit être un booléen.")
    if "orientation" in options and options.get("orientation") not in ALLOWED_ORIENTATIONS:
        errors.append(f"{path}.orientation invalide '{options.get('orientation')}'.")
    return errors


def _validate_format_types(path: str, fmt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if "type" in fmt and fmt.get("type") not in ALLOWED_FORMAT_TYPES:
        errors.append(f"{path}.type invalide '{fmt.get('type')}'.")
    if "currency" in fmt and not isinstance(fmt.get("currency"), str):
        errors.append(f"{path}.currency doit être une chaîne.")
    if "decimals" in fmt and (not _is_integer(fmt.get("decimals")) or int(fmt["decimals"]) < 0):
        errors.append(f"{path}.decimals doit être un entier positif ou nul.")
    if fmt.get("type") == "currency" and not fmt.get("currency"):
        errors.append(f"{path}.currency est requis quand format.type vaut currency.")
    return errors


def _validate_business_rules(spec: DashboardDefinition, dataset_schema: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    columns = _column_index(dataset_schema)
    layout_columns = int(spec.get("layout", {}).get("columns") or LAYOUT_COLUMNS)
    seen_ids: set[str] = set()

    for index, widget in enumerate(spec.get("widgets") or []):
        path = f"$.widgets[{index}]"
        widget_id = str(widget.get("id") or "")
        widget_type = widget.get("type")
        data = widget.get("data") or {}
        position = widget.get("position") or {}

        if widget_id in seen_ids:
            errors.append(f"{path}.id identifiant dupliqué '{widget_id}'.")
        seen_ids.add(widget_id)
        if not re.match(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$", widget_id):
            errors.append(f"{path}.id doit être en snake_case: '{widget_id}'.")

        if position.get("x") + position.get("w") > layout_columns:
            errors.append(f"{path}.position.x + position.w ne doit pas dépasser {layout_columns}.")

        referenced = _referenced_columns(widget_type, data)
        for column in referenced:
            if column not in columns:
                errors.append(f"{path}: colonne inexistante '{column}'.")

        if widget_type == "kpi_card":
            metric = data.get("metric")
            aggregation = data.get("aggregation")
            if metric in columns and aggregation != "count" and not _is_numeric(columns[metric]):
                errors.append(f"{path}.data.metric doit être une colonne numérique pour {aggregation}: '{metric}'.")

        if widget_type == "bar_chart":
            x_axis = data.get("x")
            y_axis = data.get("y")
            aggregation = data.get("aggregation")
            if x_axis in columns and not (_is_categorical(columns[x_axis]) or _is_date(columns[x_axis])):
                errors.append(f"{path}.data.x doit être catégoriel, textuel, date ou datetime: '{x_axis}'.")
            if y_axis in columns and aggregation != "count" and not _is_numeric(columns[y_axis]):
                errors.append(f"{path}.data.y doit être une colonne numérique pour {aggregation}: '{y_axis}'.")

        if widget_type == "line_chart":
            x_axis = data.get("x")
            y_axis = data.get("y")
            if x_axis in columns and not (_is_date(columns[x_axis]) or _is_ordered(columns[x_axis])):
                errors.append(f"{path}.data.x doit être date, datetime ou ordonnée: '{x_axis}'.")
            if y_axis in columns and not _is_numeric(columns[y_axis]):
                errors.append(f"{path}.data.y doit être une colonne numérique: '{y_axis}'.")

        if widget_type == "pie_chart":
            category = data.get("category")
            value = data.get("value")
            if category in columns and not _is_categorical(columns[category]):
                errors.append(f"{path}.data.category doit être catégoriel ou textuel: '{category}'.")
            if value in columns and not _is_numeric(columns[value]):
                errors.append(f"{path}.data.value doit être une colonne numérique: '{value}'.")

        if widget_type == "data_table":
            table_columns = data.get("columns") or []
            for column in table_columns:
                if column not in columns:
                    errors.append(f"{path}.data.columns contient une colonne inexistante '{column}'.")

        if widget_type == "filter":
            metric = data.get("metric")
            if metric and metric not in columns:
                errors.append(f"{path}.data.metric colonne inexistante '{metric}'.")

    return errors


def _referenced_columns(widget_type: str, data: dict[str, Any]) -> list[str]:
    if widget_type == "kpi_card":
        return [data.get("metric")]
    if widget_type in {"bar_chart", "line_chart"}:
        return [data.get("x"), data.get("y")]
    if widget_type == "pie_chart":
        return [data.get("category"), data.get("value")]
    if widget_type == "data_table":
        return list(data.get("columns") or [])
    if widget_type == "filter":
        return [data.get("metric")]
    return []


def _column_index(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    columns = schema.get("columns") if isinstance(schema, dict) else None
    if not isinstance(columns, list):
        return index
    for col in columns:
        if not isinstance(col, dict):
            continue
        name = col.get("name") or col.get("normalized_name")
        if name:
            index[str(name)] = col
    return index


def _normalise_lookup_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _column_lookup(columns: dict[str, dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for name, column in columns.items():
        for value in (name, column.get("label"), column.get("original_name")):
            key = _normalise_lookup_key(value)
            if key:
                lookup[key] = name
    return lookup


def _resolve_column(value: Any, columns: dict[str, dict[str, Any]], lookup: dict[str, str]) -> str | None:
    if not value:
        return None
    value = str(value)
    if value in columns:
        return value
    key = _normalise_lookup_key(value)
    return lookup.get(key)


def _semantic(col: dict[str, Any]) -> str:
    return str(col.get("semantic_type") or col.get("semantic_role") or "").lower().strip()


def _detected_type(col: dict[str, Any]) -> str:
    return str(col.get("type") or col.get("detected_type") or "").lower().strip()


def _is_numeric(col: dict[str, Any]) -> bool:
    return _semantic(col) == "measure" or _detected_type(col) in {"number", "currency"}


def _is_date(col: dict[str, Any]) -> bool:
    return _semantic(col) in {"date", "datetime"} or _detected_type(col) in {"date", "datetime"}


def _is_categorical(col: dict[str, Any]) -> bool:
    return _semantic(col) in {"dimension", "attribute", "identifier"} or _detected_type(col) in {
        "category",
        "text",
        "identifier",
        "boolean",
    }


def _is_ordered(col: dict[str, Any]) -> bool:
    return _is_numeric(col) or _is_date(col)


def _unknown_keys(path: str, data: dict[str, Any], allowed: set[str]) -> list[str]:
    return [f"{path}: propriété non autorisée '{key}'." for key in data if key not in allowed]


def _require(path: str, data: dict[str, Any], required: set[str]) -> list[str]:
    return [f"{path}: champ obligatoire manquant '{key}'." for key in sorted(required) if key not in data]


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
