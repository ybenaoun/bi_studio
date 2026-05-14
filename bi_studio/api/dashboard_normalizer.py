"""Anti-redundancy normalization for dashboard.v1 widget specs.

Entry point:
    replace_redundant_widgets(spec, column_metadata, user_intent) -> spec

Analytical signature encodes what a widget computes:
  kpi_card  → "kpi|{source}|{metric}|{aggregation}"
  bar/pie   → "chart|{source}|{dimension}|{metric}|{aggregation}"
  line      → "chart|{source}|{x_axis}|{metric}|{aggregation}"

filter and data_table are never deduplicated.
"""
from __future__ import annotations

import copy
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# Widget types excluded from deduplication
_NON_DEDUP_TYPES = {"filter", "data_table"}

# Higher score = preferred when signatures collide
_TYPE_PRIORITY: dict[str, int] = {
    "line_chart": 4,
    "bar_chart": 3,
    "kpi_card": 3,
    "pie_chart": 1,
}

_NUMERIC_TYPES = {"number", "currency", "numeric"}
_NUMERIC_SEMANTICS = {"measure", "currency"}
_CATEGORICAL_TYPES = {"category", "categorical", "text", "boolean", "identifier"}
_CATEGORICAL_SEMANTICS = {"dimension", "attribute", "identifier"}
_TEMPORAL_TYPES = {"date", "datetime", "temporal"}
_TEMPORAL_SEMANTICS = {"date", "datetime", "temporal"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def replace_redundant_widgets(
    spec: dict[str, Any],
    column_metadata: list[dict[str, Any]],
    user_intent: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Replace redundant analytical widgets in a dashboard.v1 spec.

    Two widgets are redundant when they share the same analytical signature
    (same source, dimension, metric, aggregation). For each redundant group:
    - the best widget is kept (priority: line > bar/kpi > pie)
    - the others are replaced by new useful widgets based on column_metadata
      and user_intent, or kept with a _warnings marker if no replacement found

    Pure and idempotent: never calls any external service.

    Args:
        spec:            A dashboard.v1 dict (will be deep-copied).
        column_metadata: List of column metadata dicts from ETL profiler.
        user_intent:     Optional user choices (analysis_goals, preferred_kpis,
                         preferred_dimensions, preferred_visualizations).

    Returns:
        A new dashboard.v1 dict with no redundant analytical signatures.
    """
    if not isinstance(spec, dict) or not column_metadata:
        return spec

    spec = copy.deepcopy(spec)
    widgets = spec.get("widgets")
    if not isinstance(widgets, list) or not widgets:
        return spec

    user_intent = user_intent or {}
    col_index = _build_col_index(column_metadata)

    logger.info(
        "dashboard_normalizer: analyse %d widgets, %d colonnes disponibles",
        len(widgets),
        len(col_index),
    )

    # Build signature → [(original_index, widget)] groups
    sig_groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for i, w in enumerate(widgets):
        sig = _widget_signature(w)
        if sig is None:
            continue  # filter / data_table — never dedup
        sig_groups.setdefault(sig, []).append((i, w))

    # Track all occupied signatures for replacement lookup
    occupied: set[str] = set(sig_groups.keys())

    # Track metrics and dimensions already rendered
    used_metrics: set[str] = set()
    used_dimensions: set[str] = set()
    for w in widgets:
        _track_usage(w, used_metrics, used_dimensions)

    duplicates = {sig: grp for sig, grp in sig_groups.items() if len(grp) > 1}
    logger.info(
        "dashboard_normalizer: %d signatures analytiques, %d doublons détectés",
        len(sig_groups),
        len(duplicates),
    )

    replacements: dict[int, dict[str, Any]] = {}

    for sig, group in duplicates.items():
        logger.info(
            "dashboard_normalizer: signature dupliquée '%s' (%d widgets)",
            sig,
            len(group),
        )
        best_i, _ = _best_widget(group)

        for idx, w in group:
            if idx == best_i:
                logger.info(
                    "dashboard_normalizer: conservation du widget '%s' (%s) — meilleure priorité",
                    w.get("id"),
                    w.get("type"),
                )
                continue

            replacement = _find_replacement(
                w, col_index, occupied, used_metrics, used_dimensions, user_intent
            )

            if replacement:
                new_sig = _widget_signature(replacement)
                if new_sig:
                    occupied.add(new_sig)
                _track_usage(replacement, used_metrics, used_dimensions)
                replacements[idx] = replacement
                logger.info(
                    "dashboard_normalizer: remplacement '%s' → '%s' (%s) | raison: doublon de '%s'",
                    w.get("id"),
                    replacement.get("id"),
                    replacement.get("type"),
                    sig,
                )
            else:
                # No replacement available — keep with warning, do not silently drop
                w_copy = copy.deepcopy(w)
                w_copy.setdefault("_warnings", []).append(
                    f"widget_redondant: signature {sig!r}"
                )
                replacements[idx] = w_copy
                logger.warning(
                    "dashboard_normalizer: aucun remplacement trouvé pour '%s' "
                    "(sig='%s') — conservé avec avertissement",
                    w.get("id"),
                    sig,
                )

    new_widgets = [replacements.get(i, w) for i, w in enumerate(widgets)]
    spec["widgets"] = new_widgets

    replaced = sum(1 for i, r in replacements.items() if "_warnings" not in r)
    kept_redundant = sum(1 for r in replacements.values() if "_warnings" in r)
    logger.info(
        "dashboard_normalizer: terminé — %d remplacés, %d redondants sans remplacement, %d widgets au total",
        replaced,
        kept_redundant,
        len(new_widgets),
    )
    return spec


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------

def compute_widget_signature(widget: dict[str, Any]) -> str | None:
    """Public alias for _widget_signature (used by tests)."""
    return _widget_signature(widget)


def _widget_signature(widget: dict[str, Any]) -> str | None:
    """Return analytical signature or None for non-deduplicated widget types."""
    wtype = widget.get("type")
    if wtype in _NON_DEDUP_TYPES:
        return None

    data = widget.get("data") or {}
    src = data.get("source") or "main"
    agg = data.get("aggregation") or "sum"

    if wtype == "kpi_card":
        metric = data.get("metric") or ""
        return f"kpi|{src}|{metric}|{agg}"

    if wtype in {"bar_chart", "pie_chart"}:
        # bar_chart uses x/y; pie_chart uses category/value — same analytical intent
        dim = data.get("x") or data.get("category") or ""
        metric = data.get("y") or data.get("value") or ""
        return f"chart|{src}|{dim}|{metric}|{agg}"

    if wtype == "line_chart":
        x = data.get("x") or ""
        y = data.get("y") or ""
        return f"chart|{src}|{x}|{y}|{agg}"

    return None


def _best_widget(group: list[tuple[int, dict[str, Any]]]) -> tuple[int, dict[str, Any]]:
    """Pick the highest-priority widget from a redundant group."""
    return max(group, key=lambda x: _TYPE_PRIORITY.get(x[1].get("type") or "", 0))


def _track_usage(
    widget: dict[str, Any],
    used_metrics: set[str],
    used_dimensions: set[str],
) -> None:
    d = widget.get("data") or {}
    for field in ("metric", "y", "value"):
        val = d.get(field)
        if val:
            used_metrics.add(val)
    for field in ("x", "category"):
        val = d.get(field)
        if val:
            used_dimensions.add(val)


# ---------------------------------------------------------------------------
# Column helpers
# ---------------------------------------------------------------------------

def _build_col_index(column_metadata: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        c["name"]: c
        for c in column_metadata
        if isinstance(c, dict) and c.get("name")
    }


def _col_type(col: dict[str, Any]) -> str:
    return str(col.get("type") or col.get("detected_type") or "").lower()


def _col_semantic(col: dict[str, Any]) -> str:
    return str(col.get("semantic_type") or col.get("semantic_role") or "").lower()


def _is_numeric(col: dict[str, Any]) -> bool:
    return _col_type(col) in _NUMERIC_TYPES or _col_semantic(col) in _NUMERIC_SEMANTICS


def _is_categorical(col: dict[str, Any]) -> bool:
    return _col_type(col) in _CATEGORICAL_TYPES or _col_semantic(col) in _CATEGORICAL_SEMANTICS


def _is_temporal(col: dict[str, Any]) -> bool:
    return _col_type(col) in _TEMPORAL_TYPES or _col_semantic(col) in _TEMPORAL_SEMANTICS


def _is_identifier(col: dict[str, Any]) -> bool:
    return _col_type(col) == "identifier" or _col_semantic(col) == "identifier"


def _col_label(col: dict[str, Any]) -> str:
    label = str(col.get("label") or col.get("name") or "")
    return label.replace("_", " ").strip().capitalize()


def _format_for(col: dict[str, Any]) -> dict[str, Any]:
    if _col_semantic(col) == "currency" or _col_type(col) == "currency":
        return {"type": "currency", "currency": "EUR", "decimals": 2}
    return {"type": "number", "decimals": 2}


# ---------------------------------------------------------------------------
# Replacement engine
# ---------------------------------------------------------------------------

def _find_replacement(
    original: dict[str, Any],
    col_index: dict[str, dict[str, Any]],
    occupied: set[str],
    used_metrics: set[str],
    used_dimensions: set[str],
    user_intent: dict[str, Any],
) -> dict[str, Any] | None:
    """Return the best replacement widget for a redundant one, or None.

    Priority order:
    1. line_chart on a temporal column + unused numeric metric
    2. bar_chart: unused metric by original dimension
    3. bar_chart: unused metric by preferred (user_intent) unused dimension
    4. bar_chart: unused metric by any unused dimension
    5. kpi_card on an unused numeric metric
    6. bar_chart: count by identifier column grouped by a categorical dimension
    """
    pos = original.get("position") or {"x": 0, "y": 0, "w": 6, "h": 4}
    existing_data = original.get("data") or {}
    existing_dim = existing_data.get("x") or existing_data.get("category")

    numeric_cols = [n for n, c in col_index.items() if _is_numeric(c)]
    categorical_cols = [n for n, c in col_index.items() if _is_categorical(c) and not _is_identifier(c)]
    temporal_cols = [n for n, c in col_index.items() if _is_temporal(c)]
    identifier_cols = [n for n, c in col_index.items() if _is_identifier(c)]

    unused_metrics = [m for m in numeric_cols if m not in used_metrics]
    unused_dims = [d for d in categorical_cols if d not in used_dimensions]

    preferred_dims = _string_list(user_intent.get("preferred_dimensions"))
    unused_preferred_dims = [
        d for d in preferred_dims if d in col_index and d not in used_dimensions
    ]

    # 1. Temporal analysis: line_chart for unused metric along a date column
    if temporal_cols and unused_metrics:
        date_col = temporal_cols[0]
        metric = unused_metrics[0]
        sig = f"chart|main|{date_col}|{metric}|sum"
        if sig not in occupied:
            return _make_line_chart(
                _uid(f"{metric}_trend"),
                f"Évolution {_col_label(col_index.get(metric, {}))}",
                date_col,
                metric,
                pos,
            )

    # 2. bar_chart: unused metric, same dimension as the original widget
    if existing_dim and unused_metrics:
        metric = unused_metrics[0]
        sig = f"chart|main|{existing_dim}|{metric}|sum"
        if sig not in occupied and existing_dim in col_index:
            dim_label = _col_label(col_index.get(existing_dim, {}))
            return _make_bar_chart(
                _uid(f"{metric}_by_{existing_dim}"),
                f"{_col_label(col_index.get(metric, {}))} par {dim_label}",
                existing_dim,
                metric,
                pos,
            )

    # 3. bar_chart: unused metric, preferred unused dimension
    if unused_preferred_dims and unused_metrics:
        dim = unused_preferred_dims[0]
        metric = unused_metrics[0]
        sig = f"chart|main|{dim}|{metric}|sum"
        if sig not in occupied:
            return _make_bar_chart(
                _uid(f"{metric}_by_{dim}"),
                f"{_col_label(col_index.get(metric, {}))} par {_col_label(col_index.get(dim, {}))}",
                dim,
                metric,
                pos,
            )

    # 4. bar_chart: unused metric by any unused dimension
    if unused_dims and unused_metrics:
        dim = unused_dims[0]
        metric = unused_metrics[0]
        sig = f"chart|main|{dim}|{metric}|sum"
        if sig not in occupied:
            return _make_bar_chart(
                _uid(f"{metric}_by_{dim}"),
                f"{_col_label(col_index.get(metric, {}))} par {_col_label(col_index.get(dim, {}))}",
                dim,
                metric,
                pos,
            )

    # 5. kpi_card for an unused numeric metric
    if unused_metrics:
        metric = unused_metrics[0]
        sig = f"kpi|main|{metric}|sum"
        if sig not in occupied:
            col = col_index.get(metric, {})
            return _make_kpi_card(
                _uid(f"total_{metric}"),
                f"Total {_col_label(col)}",
                metric,
                "sum",
                _format_for(col),
                {**pos, "w": 3, "h": 2},
            )

    # 6. count by categorical dimension using identifier column
    if identifier_cols and categorical_cols:
        id_col = identifier_cols[0]
        for dim in categorical_cols:
            sig = f"chart|main|{dim}|{id_col}|count"
            if sig not in occupied:
                return _make_bar_chart(
                    _uid(f"count_by_{dim}"),
                    f"Nombre par {_col_label(col_index.get(dim, {}))}",
                    dim,
                    id_col,
                    pos,
                    aggregation="count",
                )

    return None  # Fallback: no useful replacement found


# ---------------------------------------------------------------------------
# Widget factories (schema-compliant dashboard.v1 objects)
# ---------------------------------------------------------------------------

def _make_kpi_card(
    widget_id: str,
    title: str,
    metric: str,
    aggregation: str = "sum",
    fmt: dict[str, Any] | None = None,
    position: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": widget_id,
        "type": "kpi_card",
        "title": title,
        "position": position or {"x": 0, "y": 0, "w": 3, "h": 2},
        "data": {"source": "main", "metric": metric, "aggregation": aggregation},
        "format": fmt or {"type": "number", "decimals": 2},
    }


def _make_bar_chart(
    widget_id: str,
    title: str,
    x: str,
    y: str,
    position: dict[str, Any] | None = None,
    aggregation: str = "sum",
) -> dict[str, Any]:
    return {
        "id": widget_id,
        "type": "bar_chart",
        "title": title,
        "position": position or {"x": 0, "y": 0, "w": 6, "h": 4},
        "data": {"source": "main", "x": x, "y": y, "aggregation": aggregation},
        "options": {"orientation": "vertical", "show_legend": True, "stacked": False},
    }


def _make_line_chart(
    widget_id: str,
    title: str,
    x: str,
    y: str,
    position: dict[str, Any] | None = None,
    aggregation: str = "sum",
) -> dict[str, Any]:
    return {
        "id": widget_id,
        "type": "line_chart",
        "title": title,
        "position": position or {"x": 0, "y": 0, "w": 6, "h": 4},
        "data": {"source": "main", "x": x, "y": y, "aggregation": aggregation},
        "options": {"show_legend": True},
    }


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------

def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(x) for x in value if x]
    return []


def _uid(base: str) -> str:
    """Convert an arbitrary string to a valid snake_case widget id."""
    text = str(base or "widget").strip().lower()
    text = "".join(
        c for c in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(c)
    )
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not text or not re.match(r"^[a-z]", text):
        text = f"w_{text}" if text else "widget"
    return text
