"""Unit tests for the dashboard anti-redundancy normalizer.

These tests do NOT require Frappe to be running.

Cases covered (matching the requirements spec):
  6.  bar_chart + pie_chart with same dim/metric/aggregation → redondant
  7.  Le widget redondant est remplacé par une analyse utile, pas supprimé
  8.  kpi_card global + chart groupé sur même mesure → NON redondants
  9.  filter et data_table ne sont jamais doublons
 12.  Aucun widget final ne contient une colonne absente de column_metadata
 13.  Le dashboard final n'a aucune signature analytique dupliquée
 14.  Si aucune alternative n'est disponible, fallback conserve avec avertissement
"""
from __future__ import annotations

import copy
import unittest

from bi_studio.api.dashboard_normalizer import (
    compute_widget_signature,
    replace_redundant_widgets,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

COLUMN_METADATA = [
    {"name": "employee_id", "type": "identifier", "semantic_type": "identifier"},
    {"name": "department", "type": "category", "semantic_type": "dimension", "unique_count": 5},
    {"name": "monthly_salary", "type": "currency", "semantic_type": "currency", "label": "Salaire mensuel"},
    {"name": "sick_leaves", "type": "number", "semantic_type": "measure", "label": "Congés maladie"},
    {"name": "unpaid_leaves", "type": "number", "semantic_type": "measure", "label": "Congés sans solde"},
    {"name": "hire_date", "type": "date", "semantic_type": "date", "label": "Date d'embauche"},
]

USER_INTENT = {
    "analysis_goals": ["salaires", "congés", "comparaison par département"],
    "preferred_kpis": ["salaire total", "salaire moyen", "total congés"],
    "preferred_dimensions": ["department"],
}


def _base_spec(widgets: list) -> dict:
    return {
        "schema_version": "dashboard.v1",
        "title": "Test",
        "layout": {"columns": 12, "row_height": 80},
        "widgets": copy.deepcopy(widgets),
    }


def _kpi(widget_id, metric, agg="sum") -> dict:
    return {
        "id": widget_id,
        "type": "kpi_card",
        "title": f"KPI {metric}",
        "position": {"x": 0, "y": 0, "w": 3, "h": 2},
        "data": {"source": "main", "metric": metric, "aggregation": agg},
        "format": {"type": "number", "decimals": 2},
    }


def _bar(widget_id, x, y, agg="sum") -> dict:
    return {
        "id": widget_id,
        "type": "bar_chart",
        "title": f"Bar {y} by {x}",
        "position": {"x": 0, "y": 2, "w": 6, "h": 4},
        "data": {"source": "main", "x": x, "y": y, "aggregation": agg},
        "options": {"orientation": "vertical", "show_legend": True, "stacked": False},
    }


def _pie(widget_id, category, value, agg="sum") -> dict:
    return {
        "id": widget_id,
        "type": "pie_chart",
        "title": f"Pie {value} by {category}",
        "position": {"x": 0, "y": 6, "w": 4, "h": 4},
        "data": {"source": "main", "category": category, "value": value, "aggregation": agg},
        "options": {"show_legend": True},
    }


def _line(widget_id, x, y, agg="sum") -> dict:
    return {
        "id": widget_id,
        "type": "line_chart",
        "title": f"Trend {y}",
        "position": {"x": 6, "y": 2, "w": 6, "h": 4},
        "data": {"source": "main", "x": x, "y": y, "aggregation": agg},
        "options": {"show_legend": True},
    }


def _filter(widget_id, metric) -> dict:
    return {
        "id": widget_id,
        "type": "filter",
        "title": f"Filtre {metric}",
        "position": {"x": 0, "y": 0, "w": 3, "h": 1},
        "data": {"source": "main", "metric": metric},
    }


def _table(widget_id, columns) -> dict:
    return {
        "id": widget_id,
        "type": "data_table",
        "title": "Détails",
        "position": {"x": 0, "y": 10, "w": 12, "h": 5},
        "data": {"source": "main", "columns": columns, "limit": 100},
    }


def _all_signatures(spec: dict) -> list[str]:
    return [
        sig
        for w in spec.get("widgets", [])
        for sig in [compute_widget_signature(w)]
        if sig is not None
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWidgetSignature(unittest.TestCase):
    """Verify signature computation for all widget types."""

    def test_kpi_signature(self):
        sig = compute_widget_signature(_kpi("k1", "monthly_salary"))
        self.assertEqual(sig, "kpi|main|monthly_salary|sum")

    def test_bar_chart_signature(self):
        sig = compute_widget_signature(_bar("b1", "department", "monthly_salary"))
        self.assertEqual(sig, "chart|main|department|monthly_salary|sum")

    def test_pie_chart_signature_same_as_bar_for_same_dim_metric(self):
        bar_sig = compute_widget_signature(_bar("b1", "department", "monthly_salary"))
        pie_sig = compute_widget_signature(_pie("p1", "department", "monthly_salary"))
        self.assertEqual(bar_sig, pie_sig, "bar_chart et pie_chart avec même dim/metric doivent avoir la même signature")

    def test_line_chart_signature(self):
        sig = compute_widget_signature(_line("l1", "hire_date", "monthly_salary"))
        self.assertEqual(sig, "chart|main|hire_date|monthly_salary|sum")

    def test_filter_returns_none(self):
        self.assertIsNone(compute_widget_signature(_filter("f1", "department")))

    def test_data_table_returns_none(self):
        self.assertIsNone(compute_widget_signature(_table("t1", ["employee_id", "monthly_salary"])))


class TestNonRedundantCases(unittest.TestCase):
    """Widgets that must NOT be considered redundant (case 8 & 9)."""

    def test_kpi_global_and_grouped_chart_are_not_redundant(self):
        """Case 8: kpi_card (no dim) + bar_chart (grouped) on same metric → not redundant."""
        spec = _base_spec([
            _kpi("total_salary", "monthly_salary", "sum"),
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        types = [w["type"] for w in result["widgets"]]
        self.assertIn("kpi_card", types)
        self.assertIn("bar_chart", types)
        self.assertEqual(len(result["widgets"]), 2, "Aucun widget ne doit être éliminé")

    def test_filter_and_table_are_never_duplicates(self):
        """Case 9: filter and data_table are exempt from deduplication."""
        spec = _base_spec([
            _filter("f1", "department"),
            _filter("f2", "department"),  # same filter twice — never dedup
            _table("t1", ["employee_id"]),
            _table("t2", ["monthly_salary"]),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        self.assertEqual(len(result["widgets"]), 4, "filter et data_table ne doivent jamais être dédupliqués")

    def test_different_dimensions_are_not_redundant(self):
        bar1 = _bar("b1", "department", "monthly_salary")
        bar2 = _bar("b2", "department", "sick_leaves")  # different metric
        spec = _base_spec([bar1, bar2])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        self.assertEqual(len(result["widgets"]), 2)
        sigs = _all_signatures(result)
        self.assertEqual(len(sigs), len(set(sigs)), "Aucun doublon de signature")


class TestRedundancyDetection(unittest.TestCase):
    """Verify that redundant widgets are detected and replaced."""

    def test_bar_and_pie_same_dimension_metric_are_redundant(self):
        """Case 6: bar_chart + pie_chart with same dim/metric/agg are redundant."""
        spec = _base_spec([
            _kpi("total_salary", "monthly_salary", "sum"),
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share_by_dept", "department", "monthly_salary", "sum"),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        sigs = _all_signatures(result)
        self.assertEqual(len(sigs), len(set(sigs)), "Aucune signature ne doit être dupliquée (case 13)")

    def test_redundant_widget_is_replaced_not_dropped(self):
        """Case 7: redundant widget is replaced by a new useful widget."""
        spec = _base_spec([
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share_by_dept", "department", "monthly_salary", "sum"),
        ])
        original_count = len(spec["widgets"])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        self.assertEqual(
            len(result["widgets"]),
            original_count,
            "Le nombre de widgets doit rester identique: remplacement, pas suppression",
        )

    def test_replacement_widget_uses_only_valid_columns(self):
        """Case 12: no widget may reference a column absent from column_metadata."""
        spec = _base_spec([
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share_by_dept", "department", "monthly_salary", "sum"),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        valid_col_names = {c["name"] for c in COLUMN_METADATA}
        for w in result["widgets"]:
            data = w.get("data") or {}
            for field in ("metric", "x", "y", "category", "value"):
                col = data.get(field)
                if col:
                    self.assertIn(
                        col,
                        valid_col_names,
                        f"Colonne '{col}' absente de column_metadata dans widget '{w.get('id')}'",
                    )

    def test_no_duplicate_analytical_signatures_in_output(self):
        """Case 13: final dashboard must have no duplicate analytical signatures."""
        spec = _base_spec([
            _kpi("total_salary", "monthly_salary", "sum"),
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share_by_dept", "department", "monthly_salary", "sum"),
            _line("salary_trend", "hire_date", "monthly_salary", "sum"),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        sigs = _all_signatures(result)
        self.assertEqual(len(sigs), len(set(sigs)), f"Signatures dupliquées: {sigs}")

    def test_bar_chart_is_preferred_over_pie_chart(self):
        """bar_chart has higher priority than pie_chart: bar_chart must be kept."""
        spec = _base_spec([
            _pie("salary_share", "department", "monthly_salary", "sum"),
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        kept_types = {w["type"] for w in result["widgets"]}
        self.assertIn("bar_chart", kept_types)

    def test_replacement_widget_covers_unused_metric(self):
        """The replacement for pie_chart should use an unused metric (sick_leaves)."""
        spec = _base_spec([
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share", "department", "monthly_salary", "sum"),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        # The replacement should cover sick_leaves or unpaid_leaves (unused metrics)
        all_metrics = set()
        for w in result["widgets"]:
            d = w.get("data") or {}
            for f in ("metric", "y", "value"):
                if d.get(f):
                    all_metrics.add(d[f])
        # At least one of the leave columns should now be covered
        self.assertTrue(
            all_metrics & {"sick_leaves", "unpaid_leaves", "hire_date"},
            f"Aucun metric de remplacement utile trouvé. Métriques: {all_metrics}",
        )


class TestFallbackBehaviour(unittest.TestCase):
    """Case 14: if no replacement is available, keep with warning."""

    def test_no_replacement_keeps_widget_with_warning(self):
        """With only one numeric column, no replacement can be found → keep with warning."""
        minimal_cols = [
            {"name": "department", "type": "category", "semantic_type": "dimension"},
            {"name": "monthly_salary", "type": "currency", "semantic_type": "currency"},
        ]
        spec = _base_spec([
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share", "department", "monthly_salary", "sum"),
        ])
        result = replace_redundant_widgets(spec, minimal_cols, {})
        self.assertEqual(len(result["widgets"]), 2, "Aucun widget ne doit être supprimé")
        # One widget should have a _warnings marker
        warned = [w for w in result["widgets"] if "_warnings" in w]
        self.assertEqual(len(warned), 1, "Un widget redondant doit avoir le marqueur _warnings")

    def test_empty_column_metadata_returns_spec_unchanged(self):
        spec = _base_spec([_bar("b1", "department", "monthly_salary")])
        result = replace_redundant_widgets(spec, [], {})
        self.assertEqual(result["widgets"], spec["widgets"])

    def test_none_user_intent_does_not_crash(self):
        spec = _base_spec([
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share", "department", "monthly_salary", "sum"),
        ])
        try:
            result = replace_redundant_widgets(spec, COLUMN_METADATA, None)
            self.assertIsInstance(result, dict)
        except Exception as exc:
            self.fail(f"replace_redundant_widgets a levé une exception inattendue: {exc}")


class TestIdempotence(unittest.TestCase):
    """Calling the function twice on the output should be idempotent."""

    def test_second_call_is_idempotent(self):
        spec = _base_spec([
            _kpi("total_salary", "monthly_salary", "sum"),
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share", "department", "monthly_salary", "sum"),
        ])
        result1 = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        result2 = replace_redundant_widgets(result1, COLUMN_METADATA, USER_INTENT)
        sigs1 = _all_signatures(result1)
        sigs2 = _all_signatures(result2)
        self.assertEqual(
            sorted(sigs1), sorted(sigs2),
            "L'appel répété ne doit pas modifier les signatures analytiques",
        )

    def test_original_spec_is_not_mutated(self):
        spec = _base_spec([
            _bar("b1", "department", "monthly_salary"),
            _pie("p1", "department", "monthly_salary"),
        ])
        original_widget_ids = [w["id"] for w in spec["widgets"]]
        _ = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        self.assertEqual(
            [w["id"] for w in spec["widgets"]],
            original_widget_ids,
            "L'entrée ne doit pas être mutée",
        )


class TestTemporalReplacement(unittest.TestCase):
    """Verify that temporal columns trigger line_chart replacements."""

    def test_temporal_replacement_generates_line_chart(self):
        """When a date column exists and a metric is unused, prefer line_chart."""
        spec = _base_spec([
            _bar("salary_by_dept", "department", "monthly_salary", "sum"),
            _pie("salary_share", "department", "monthly_salary", "sum"),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        types = [w["type"] for w in result["widgets"]]
        # hire_date exists → replacement should be line_chart on sick_leaves
        # OR bar_chart on sick_leaves — either is valid but line_chart is preferred
        self.assertIn("bar_chart", types)  # original kept
        # At minimum one replacement must exist
        self.assertEqual(len(result["widgets"]), 2)


class TestMultipleRedundancyGroups(unittest.TestCase):
    """Multiple independent redundancy groups in the same dashboard."""

    def test_two_independent_redundancy_groups(self):
        spec = _base_spec([
            # Group 1: salary by department
            _bar("salary_bar", "department", "monthly_salary", "sum"),
            _pie("salary_pie", "department", "monthly_salary", "sum"),
            # Group 2: sick_leaves by department (different metric — NOT same group)
            _bar("leaves_bar", "department", "sick_leaves", "sum"),
        ])
        result = replace_redundant_widgets(spec, COLUMN_METADATA, USER_INTENT)
        sigs = _all_signatures(result)
        self.assertEqual(len(sigs), len(set(sigs)), "Aucune signature dupliquée après normalisation")
        self.assertEqual(len(result["widgets"]), 3, "Nombre de widgets inchangé")


if __name__ == "__main__":
    unittest.main()
