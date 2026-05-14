"""Unit tests for the new intelligent ETL + Cohere pipeline.

These tests do NOT require Frappe to be running: they exercise the pure-Python
modules (etl_cleaning, column_renamer, dataset_profiler, json_schema,
dashboard_renderer_data). The Cohere adapter is exercised via mocking.
"""
from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock, patch

import pandas as pd

from bi_studio.api.column_renamer import rename_columns_to_readable_names
from bi_studio.api.dashboard_renderer_data import (
    calculate_chart_data,
    calculate_kpi,
)
from bi_studio.api.dashboard_builder import _public_widget_type, _stored_widget_type
from bi_studio.api.excel_pipeline import _build_validated_dashboard_from_intent
from bi_studio.api.dataset_profiler import profile_dataset, schema_from_profile
from bi_studio.api.etl_cleaning import clean_dataset
from bi_studio.utils.data_quality import compute_dataset_quality
from bi_studio.utils.dashboard_intent import (
    build_dashboard_definition_from_intent,
    parse_dashboard_intent,
)
from bi_studio.utils.json_schema import (
    prepare_dashboard_spec,
    validate_dashboard_response,
    validate_dashboard_spec,
)


def _employees_df() -> pd.DataFrame:
    return pd.DataFrame({
        "No": ["E001", "E002", "E003", "E004", "E005"],
        "Name": ["Alice Martin", "Bob Diallo", "Chen Wei", "Dora Nkosi", "Eric Toure"],
        "Gender": ["F", "M", "M", "F", "M"],
        "Department": ["Sales", "Engineering", "Sales", "HR", "Engineering"],
        "Start Date": ["2024-01-15", "2023-09-01", "2024-06-01", "2022-03-12", "2023-11-30"],
        "Monthly Salary": ["3 200,00", "5,500.00", "$3,800", "2 900", "5 100,50"],
        "Annual Salary": [38400, 66000, 45600, 34800, 61206],
    })


def json_dumps(value):
    return json.dumps(value, ensure_ascii=False)


class TestEtlCleaning(unittest.TestCase):
    def test_clean_dataset_handles_currency_and_thousand_separators(self):
        df = _employees_df()
        result = clean_dataset(df)
        cleaned = result["dataframe"]
        self.assertEqual(len(cleaned.index), 5)
        # Monthly Salary should now be numeric
        salaries = pd.to_numeric(cleaned["Monthly Salary"], errors="coerce")
        self.assertTrue(salaries.notna().all(), "All salaries should parse as numbers")
        self.assertAlmostEqual(float(salaries.iloc[0]), 3200.0, places=1)

    def test_constant_column_is_flagged_not_dropped(self):
        df = _employees_df()
        df["Country"] = "France"
        result = clean_dataset(df)
        self.assertIn("Country", result["removed_constant_columns"])
        self.assertIn("Country", result["dataframe"].columns)

    def test_removes_exact_duplicate_rows(self):
        df = pd.concat([_employees_df(), _employees_df().iloc[[0]]], ignore_index=True)
        result = clean_dataset(df)
        self.assertEqual(result["duplicate_rows_removed"], 1)


class TestColumnRenamer(unittest.TestCase):
    def test_employee_columns_get_canonical_snake_case_names(self):
        df = _employees_df()
        out = rename_columns_to_readable_names(df)
        mapping = out["column_mapping"]
        self.assertEqual(mapping["No"], "employee_id")
        self.assertEqual(mapping["Name"], "employee_name")
        self.assertEqual(mapping["Monthly Salary"], "monthly_salary")
        self.assertEqual(out["column_labels"]["monthly_salary"], "Salaire mensuel")

    def test_collision_resolution_appends_suffix(self):
        df = pd.DataFrame({"Foo": [1], "foo": [2]})
        out = rename_columns_to_readable_names(df)
        cols = list(out["column_mapping"].values())
        self.assertEqual(cols[0], "foo")
        self.assertTrue(cols[1].startswith("foo_"))


class TestDatasetProfiler(unittest.TestCase):
    def test_employee_profile_detects_correct_semantics(self):
        df = clean_dataset(_employees_df())["dataframe"]
        renamed = rename_columns_to_readable_names(df)
        profile = profile_dataset(
            renamed["dataframe"],
            renamed["column_mapping"],
            renamed["column_labels"],
        )
        by_name = {col["name"]: col for col in profile["columns"]}
        self.assertEqual(by_name["employee_id"]["semantic_type"], "identifier")
        self.assertEqual(by_name["monthly_salary"]["semantic_type"], "measure")
        self.assertEqual(by_name["annual_salary"]["semantic_type"], "measure")
        self.assertEqual(by_name["start_date"]["semantic_type"], "date")
        self.assertIn(by_name["gender"]["semantic_type"], {"dimension", "attribute"})

    def test_quality_score_is_computed_not_hardcoded(self):
        df = clean_dataset(_employees_df())["dataframe"]
        renamed = rename_columns_to_readable_names(df)
        profile = profile_dataset(renamed["dataframe"], renamed["column_mapping"], renamed["column_labels"])
        quality = compute_dataset_quality(renamed["dataframe"], profile["columns"])
        self.assertGreaterEqual(quality["score"], 0)
        self.assertLessEqual(quality["score"], 100)
        # Add missing values, verify score drops
        broken = renamed["dataframe"].copy()
        broken.loc[broken.index[:3], "monthly_salary"] = None
        broken_profile = profile_dataset(broken, renamed["column_mapping"], renamed["column_labels"])
        broken_quality = compute_dataset_quality(broken, broken_profile["columns"])
        self.assertLess(broken_quality["score"], quality["score"])


class TestJsonSchemaValidator(unittest.TestCase):
    def setUp(self):
        df = clean_dataset(_employees_df())["dataframe"]
        renamed = rename_columns_to_readable_names(df)
        profile = profile_dataset(renamed["dataframe"], renamed["column_mapping"], renamed["column_labels"])
        self.schema = schema_from_profile(profile)
        self.df = renamed["dataframe"]

    def valid_dashboard(self):
        return {
            "schema_version": "dashboard.v1",
            "title": "Tableau de bord des employés",
            "layout": {"columns": 12, "row_height": 80},
            "widgets": [
                {
                    "id": "employee_count",
                    "type": "kpi_card",
                    "title": "Nombre d'employés",
                    "position": {"x": 0, "y": 0, "w": 3, "h": 2},
                    "data": {"source": "main", "metric": "employee_id", "aggregation": "count"},
                    "format": {"type": "number", "decimals": 0},
                },
                {
                    "id": "salary_by_department",
                    "type": "bar_chart",
                    "title": "Salaires par département",
                    "position": {"x": 0, "y": 2, "w": 6, "h": 4},
                    "data": {"source": "main", "x": "department", "y": "monthly_salary", "aggregation": "sum"},
                    "options": {"orientation": "vertical", "show_legend": True, "stacked": False},
                },
                {
                    "id": "salary_trend",
                    "type": "line_chart",
                    "title": "Évolution des salaires",
                    "position": {"x": 6, "y": 2, "w": 6, "h": 4},
                    "data": {"source": "main", "x": "start_date", "y": "monthly_salary", "aggregation": "avg"},
                    "options": {"show_legend": True},
                },
                {
                    "id": "salary_share",
                    "type": "pie_chart",
                    "title": "Répartition salariale",
                    "position": {"x": 0, "y": 6, "w": 4, "h": 4},
                    "data": {"source": "main", "category": "department", "value": "monthly_salary", "aggregation": "sum"},
                    "options": {"show_legend": True},
                },
                {
                    "id": "details_table",
                    "type": "data_table",
                    "title": "Détails",
                    "position": {"x": 0, "y": 10, "w": 12, "h": 5},
                    "data": {"source": "main", "columns": ["employee_id", "department", "monthly_salary"], "limit": 100},
                },
            ],
        }

    def clone(self, spec):
        import copy

        return copy.deepcopy(spec)

    def test_valid_employee_dashboard_spec_passes(self):
        spec = self.valid_dashboard()
        errors = validate_dashboard_spec(spec, self.schema)
        self.assertEqual(errors, [])

    def test_unknown_attribute_is_rejected(self):
        spec = self.valid_dashboard()
        spec["description"] = "Attribut interdit"
        errors = validate_dashboard_spec(spec, self.schema)
        self.assertTrue(any("propriété non autorisée 'description'" in e for e in errors))

    def test_aliases_are_normalized_before_validation(self):
        spec = {
            "schema_version": "dashboard.v1",
            "titre": "Tableau de bord",
            "layout": {"columns": 12, "row_height": 80},
            "widgets": [
                {
                    "id": "alias_chart",
                    "type": "bar_chart",
                    "chartTitle": "Salaires par département",
                    "position": {"x": 0, "y": 0, "w": 6, "h": 4},
                    "data": {
                        "dataSource": "main",
                        "xAxis": "department",
                        "yAxis": "monthly_salary",
                        "aggregation": "sum",
                    },
                    "options": {"orientation": "vertical", "showLegend": True, "isStacked": False},
                }
            ],
        }
        normalized = prepare_dashboard_spec(spec, self.schema)
        self.assertEqual(normalized["title"], "Tableau de bord")
        self.assertEqual(normalized["widgets"][0]["title"], "Salaires par département")
        self.assertEqual(normalized["widgets"][0]["data"]["source"], "main")
        self.assertEqual(validate_dashboard_spec(normalized, self.schema), [])

    def test_unknown_column_is_rejected(self):
        spec = self.valid_dashboard()
        spec["widgets"][0]["data"]["metric"] = "ghost_column"
        errors = validate_dashboard_spec(spec, self.schema)
        self.assertTrue(any("ghost_column" in e for e in errors))

    def test_duplicate_widget_id_is_rejected(self):
        spec = self.valid_dashboard()
        spec["widgets"][1]["id"] = "employee_count"
        errors = validate_dashboard_spec(spec, self.schema)
        self.assertTrue(any("dupliqué" in e for e in errors))

    def test_non_snake_case_id_is_rejected(self):
        spec = self.valid_dashboard()
        spec["widgets"][0]["id"] = "EmployeeCount"
        errors = validate_dashboard_spec(spec, self.schema)
        self.assertTrue(any("snake_case" in e for e in errors))

    def test_bar_chart_y_non_numeric_is_rejected(self):
        spec = self.valid_dashboard()
        spec["widgets"][1]["data"]["y"] = "department"
        errors = validate_dashboard_spec(spec, self.schema)
        self.assertTrue(any("data.y doit être une colonne numérique" in e for e in errors))

    def test_kpi_metric_non_numeric_is_rejected_except_count(self):
        spec = self.valid_dashboard()
        spec["widgets"][0]["data"] = {"source": "main", "metric": "employee_name", "aggregation": "sum"}
        errors = validate_dashboard_spec(spec, self.schema)
        self.assertTrue(any("data.metric doit être une colonne numérique" in e for e in errors))

        spec["widgets"][0]["data"]["aggregation"] = "count"
        self.assertEqual(validate_dashboard_spec(spec, self.schema), [])

    def test_markdown_around_json_is_rejected_before_repair(self):
        raw = "```json\n" + json_dumps(self.valid_dashboard()) + "\n```"
        _, errors = validate_dashboard_response(raw, self.schema)
        self.assertTrue(any("JSON brut directement parsable" in e for e in errors))

    def test_gateway_error_raw_wrapper_is_unwrapped(self):
        raw = json_dumps({
            "error": "Model response was not JSON at gateway level",
            "raw": json_dumps(self.valid_dashboard()),
        })
        spec, errors = validate_dashboard_response(raw, self.schema)
        self.assertEqual(errors, [])
        self.assertEqual(spec["schema_version"], "dashboard.v1")

    def test_backend_builds_dashboard_v1_from_intent(self):
        intent = {
            "title": "Tableau de bord des employés",
            "preferred_widgets": ["kpi_card", "bar_chart", "line_chart", "pie_chart", "data_table"],
            "main_metric": "monthly_salary",
            "main_dimension": "department",
            "date_column": "start_date",
            "table_columns": ["employee_id", "department", "monthly_salary"],
        }
        spec = build_dashboard_definition_from_intent(intent, self.schema, fallback_title="Fallback")
        self.assertEqual(validate_dashboard_spec(spec, self.schema), [])
        self.assertEqual(spec["schema_version"], "dashboard.v1")
        self.assertTrue(any(widget["type"] == "bar_chart" for widget in spec["widgets"]))

    def test_backend_builds_valid_fallback_when_intent_is_invalid(self):
        spec = build_dashboard_definition_from_intent(
            {"main_metric": "ghost", "main_dimension": "missing"},
            self.schema,
            fallback_title="Fallback",
        )
        self.assertEqual(validate_dashboard_spec(spec, self.schema), [])
        self.assertEqual(spec["title"], "Fallback")

    def test_pipeline_builds_dashboard_from_stored_intent(self):
        spec_doc = MagicMock()
        spec_doc.response_json = json_dumps({
            "title": "Dashboard intention",
            "preferred_widgets": ["kpi_card", "bar_chart", "data_table"],
            "main_metric": "monthly_salary",
            "main_dimension": "department",
        })
        spec_doc.raw_response_text = spec_doc.response_json
        out = _build_validated_dashboard_from_intent(
            {"metadata": {"dashboard_objective": "Fallback"}},
            spec_doc,
            self.schema,
        )
        self.assertEqual(out["schema_version"], "dashboard.v1")
        self.assertEqual(validate_dashboard_spec(out, self.schema), [])

    def test_pipeline_uses_deterministic_fallback_for_invalid_intent(self):
        spec_doc = MagicMock()
        spec_doc.response_json = "not json"
        spec_doc.raw_response_text = "not json"
        out = _build_validated_dashboard_from_intent(
            {"metadata": {"dashboard_objective": "Fallback"}},
            spec_doc,
            self.schema,
        )
        self.assertEqual(validate_dashboard_spec(out, self.schema), [])


class TestRendererData(unittest.TestCase):
    def setUp(self):
        df = clean_dataset(_employees_df())["dataframe"]
        renamed = rename_columns_to_readable_names(df)
        self.df = renamed["dataframe"]

    def test_kpi_count_distinct_employee_id(self):
        out = calculate_kpi(self.df, {
            "type": "kpi_card",
            "data": {"source": "main", "metric": "employee_id", "aggregation": "count"},
            "format": {"type": "number", "decimals": 0},
        })
        self.assertEqual(out["value"], 5)

    def test_kpi_sum_monthly_salary(self):
        out = calculate_kpi(self.df, {
            "type": "kpi_card",
            "data": {"source": "main", "metric": "monthly_salary", "aggregation": "sum"},
            "format": {"type": "number", "decimals": 2},
        })
        self.assertGreater(out["value"], 0)

    def test_chart_bar_groups_by_department(self):
        out = calculate_chart_data(self.df, {
            "type": "bar_chart",
            "data": {
                "source": "main",
                "x": "department",
                "y": "monthly_salary",
                "aggregation": "sum",
            },
        })
        self.assertIn("Engineering", out["labels"])
        self.assertEqual(len(out["labels"]), len(out["values"]))


class TestDashboardBuilderWidgetTypes(unittest.TestCase):
    def test_dashboard_v1_type_is_converted_to_frappe_select_value(self):
        self.assertEqual(_stored_widget_type("kpi_card"), "kpi")
        self.assertEqual(_stored_widget_type("bar_chart"), "bar")
        self.assertEqual(_stored_widget_type("line_chart"), "line")
        self.assertEqual(_stored_widget_type("pie_chart"), "pie")
        self.assertEqual(_stored_widget_type("data_table"), "table")
        self.assertEqual(_stored_widget_type("filter"), "table")

    def test_public_widget_type_prefers_valid_dashboard_v1_config_type(self):
        self.assertEqual(_public_widget_type("table", {"type": "filter"}), "filter")
        self.assertEqual(_public_widget_type("bar", {"type": "bar_chart"}), "bar_chart")
        self.assertEqual(_public_widget_type("table", {}), "table")


class TestCohereAdapterMocked(unittest.TestCase):
    """The adapter must NEVER instantiate a Cohere client. We verify it routes
    through custom_dashboard.services.ai_gateway.generate_with_cohere.
    """

    def _make_clean_doc(self, schema_json='{"columns":[]}'):
        doc = MagicMock()
        doc.name = "BI-CLEAN-00001"
        doc.dataset_title = "Test"
        doc.row_count = 5
        doc.column_count = 3
        doc.quality_score = 88.0
        doc.schema_json = schema_json
        doc.profile_json = '{"columns":[]}'
        doc.column_labels_json = '{}'
        doc.preview_json = '[]'
        return doc

    def test_adapter_calls_existing_gateway(self):
        from bi_studio.api import cohere_dashboard

        clean_dataset_doc = self._make_clean_doc()
        user_intent = {"analysis_goals": ["salaires"], "preferred_kpis": [], "preferred_dimensions": []}

        prompt = cohere_dashboard.build_cohere_dashboard_prompt(clean_dataset_doc, user_intent=user_intent)
        self.assertIn("system", prompt)
        self.assertIn("user", prompt)
        # New: prompt asks for full dashboard.v1 spec, not small intent
        self.assertIn("dashboard.v1", prompt["user"])
        self.assertIn("user_intent", prompt)
        self.assertEqual(prompt["user_intent"], user_intent)

        fake_gateway = MagicMock()
        # Simulate Cohere returning a full dashboard.v1 spec
        valid_spec = {
            "schema_version": "dashboard.v1",
            "title": "Test",
            "layout": {"columns": 12, "row_height": 80},
            "widgets": [],
        }
        fake_gateway.generate_with_cohere.return_value = {
            "response": json_dumps(valid_spec),
            "model": "command-a",
            "input_tokens": 100,
            "output_tokens": 200,
        }
        import frappe as _frappe
        fake_doc = MagicMock()
        fake_doc.name = "BI-AI-SPEC-00001"
        fake_doc.raw_response_text = ""
        with patch.object(cohere_dashboard, "_import_gateway", return_value=fake_gateway), \
             patch.object(_frappe, "new_doc", return_value=fake_doc, create=True), \
             patch.object(_frappe, "db", MagicMock(), create=True):
            spec_doc, parsed = cohere_dashboard.call_cohere_dashboard_generation(
                prompt, clean_dataset_doc=clean_dataset_doc, import_doc=None,
            )
            self.assertEqual(parsed.get("schema_version"), "dashboard.v1")
            fake_gateway.generate_with_cohere.assert_called_once()
            call_kwargs = fake_gateway.generate_with_cohere.call_args.kwargs
            self.assertEqual(call_kwargs.get("language"), "fr")
            self.assertEqual(call_kwargs.get("temperature"), 0)
            # New: max_tokens increased for full spec generation
            self.assertGreaterEqual(call_kwargs.get("max_tokens"), 2000)
            self.assertIn("system_message", call_kwargs)
            self.assertIn("user_message", call_kwargs)

    def test_prompt_includes_user_intent_section(self):
        from bi_studio.api import cohere_dashboard

        clean_dataset_doc = self._make_clean_doc()
        user_intent = {
            "analysis_goals": ["salaires", "congés"],
            "preferred_kpis": ["monthly_salary"],
            "preferred_dimensions": ["department"],
        }
        prompt = cohere_dashboard.build_cohere_dashboard_prompt(clean_dataset_doc, user_intent=user_intent)
        self.assertIn("salaires", prompt["user"])
        self.assertIn("congés", prompt["user"])
        self.assertIn("monthly_salary", prompt["user"])

    def test_prompt_without_user_intent_still_works(self):
        from bi_studio.api import cohere_dashboard

        clean_dataset_doc = self._make_clean_doc()
        prompt = cohere_dashboard.build_cohere_dashboard_prompt(clean_dataset_doc)
        self.assertIn("dashboard.v1", prompt["user"])
        self.assertEqual(prompt["user_intent"], {})

    def test_prompt_stays_under_char_limit(self):
        from bi_studio.api import cohere_dashboard

        columns = [
            {
                "name": f"very_long_column_name_{idx}_with_business_context",
                "type": "number" if idx % 3 == 0 else "category",
                "semantic_type": "measure" if idx % 3 == 0 else "dimension",
                "label": f"Libellé métier très descriptif pour la colonne {idx}",
            }
            for idx in range(600)
        ]
        clean_dataset_doc = self._make_clean_doc(schema_json=json_dumps({"columns": columns}))
        clean_dataset_doc.column_count = len(columns)

        prompt = cohere_dashboard.build_cohere_dashboard_prompt(clean_dataset_doc)
        self.assertLessEqual(len(prompt["user"]), 14000)
        self.assertIn("dashboard.v1", prompt["user"])

    def test_parse_intent_unwraps_gateway_raw(self):
        """Legacy small-intent parsing still works for backward compat."""
        raw = json_dumps({
            "error": "parse failed",
            "raw": json_dumps({"title": "Test", "preferred_widgets": ["data_table"]}),
        })
        intent = parse_dashboard_intent(raw)
        self.assertEqual(intent["title"], "Test")
        self.assertEqual(intent["preferred_widgets"], ["data_table"])

    def test_pipeline_handles_full_dashboard_v1_from_cohere(self):
        """If Cohere returns a full dashboard.v1, _build_validated_dashboard_from_intent uses it."""
        df = clean_dataset(_employees_df())["dataframe"]
        renamed = rename_columns_to_readable_names(df)
        profile = profile_dataset(renamed["dataframe"], renamed["column_mapping"], renamed["column_labels"])
        schema = schema_from_profile(profile)

        full_spec = {
            "schema_version": "dashboard.v1",
            "title": "Test pipeline",
            "layout": {"columns": 12, "row_height": 80},
            "widgets": [
                {
                    "id": "employee_count",
                    "type": "kpi_card",
                    "title": "Nombre d'employés",
                    "position": {"x": 0, "y": 0, "w": 3, "h": 2},
                    "data": {"source": "main", "metric": "employee_id", "aggregation": "count"},
                    "format": {"type": "number", "decimals": 0},
                },
                {
                    "id": "salary_by_department",
                    "type": "bar_chart",
                    "title": "Salaires par département",
                    "position": {"x": 0, "y": 2, "w": 6, "h": 4},
                    "data": {"source": "main", "x": "department", "y": "monthly_salary", "aggregation": "sum"},
                    "options": {"orientation": "vertical", "show_legend": True, "stacked": False},
                },
            ],
        }
        spec_doc = MagicMock()
        spec_doc.response_json = json_dumps(full_spec)
        spec_doc.raw_response_text = spec_doc.response_json

        from bi_studio.api.excel_pipeline import _build_validated_dashboard_from_intent
        out = _build_validated_dashboard_from_intent(
            {"metadata": {"dashboard_objective": "Test"}},
            spec_doc,
            schema,
            user_intent={"analysis_goals": ["salaires"]},
        )
        self.assertEqual(out["schema_version"], "dashboard.v1")
        # Verify no duplicate signatures
        from bi_studio.api.dashboard_normalizer import compute_widget_signature
        sigs = [compute_widget_signature(w) for w in out.get("widgets", []) if compute_widget_signature(w)]
        self.assertEqual(len(sigs), len(set(sigs)), "Aucune signature dupliquée")


if __name__ == "__main__":
    unittest.main()
