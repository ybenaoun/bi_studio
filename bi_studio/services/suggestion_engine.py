import frappe
from frappe.utils import now_datetime

from bi_studio.config import DEFAULT_TOP_N
from bi_studio.services.query import get_chart_data, get_kpi_value
from bi_studio.services.serialization import to_json
from bi_studio.utils.deprecation import warn_deprecated


LEGACY_GENERATOR_FUNCTIONS_DEPRECATED = True


def clear_generated_definitions(dataset_name):
    for doctype in ["BI KPI Definition", "BI Chart Definition"]:
        for name in frappe.get_all(doctype, filters={"dataset": dataset_name}, pluck="name"):
            frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def generate_definitions(dataset_doc, profiles):
    warn_deprecated(
        "bi_studio.services.suggestion_engine.generate_definitions",
        "bi_studio.api.dashboard_generator.generate_dashboard",
    )
    clear_generated_definitions(dataset_doc.name)
    numeric = [profile for profile in profiles if is_safe_measure(profile)]
    dates = [profile for profile in profiles if profile.is_date]
    categories = [profile for profile in profiles if profile.is_category and not profile.is_date]

    kpis = []
    for index, profile in enumerate(numeric[:5], start=1):
        title = f"Total {profile.label}"
        value = get_kpi_value(dataset_doc, {"value_field": profile.name, "calculation_type": "Total"})
        doc = frappe.get_doc(
            {
                "doctype": "BI KPI Definition",
                "dataset": dataset_doc.name,
                "label": title,
                "source_column": profile.name,
                "calculation_type": "Total",
                "format_type": "Number",
                "display_order": index,
                "value_json": to_json({"value": value}),
            }
        )
        doc.insert(ignore_permissions=True)
        kpis.append(doc)

    charts = []
    order = 1
    if dates and numeric:
        charts.append(
            create_chart_definition(
                dataset_doc,
                title=f"Evolution de {numeric[0].label}",
                chart_type="Line",
                category_field=dates[0].name,
                value_field=numeric[0].name,
                calculation_type="Total",
                order=order,
            )
        )
        order += 1

    if categories and numeric:
        charts.append(
            create_chart_definition(
                dataset_doc,
                title=f"Top {categories[0].label}",
                chart_type="Bar",
                category_field=categories[0].name,
                value_field=numeric[0].name,
                calculation_type="Total",
                top_n=DEFAULT_TOP_N,
                order=order,
            )
        )
        order += 1

        low_cardinality = next((category for category in categories if category.distinct_count <= 8), None)
        if low_cardinality:
            charts.append(
                create_chart_definition(
                    dataset_doc,
                    title=f"Repartition par {low_cardinality.label}",
                    chart_type="Donut",
                    category_field=low_cardinality.name,
                    value_field=numeric[0].name,
                    calculation_type="Total",
                    top_n=8,
                    order=order,
                )
            )
            order += 1

    if numeric:
        charts.append(
            create_chart_definition(
                dataset_doc,
                title=f"Distribution de {numeric[0].label}",
                chart_type="Histogram",
                value_field=numeric[0].name,
                calculation_type="Nombre",
                order=order,
            )
        )
        order += 1

    if len(numeric) >= 2 and (dates or categories):
        category = dates[0] if dates else categories[0]
        charts.append(
            create_chart_definition(
                dataset_doc,
                title=f"Comparaison {numeric[0].label} et {numeric[1].label}",
                chart_type="Combined",
                category_field=category.name,
                value_field=numeric[0].name,
                secondary_value_field=numeric[1].name,
                calculation_type="Total",
                order=order,
            )
        )

    return kpis, charts


def is_safe_measure(profile):
    name = f"{getattr(profile, 'name', '')} {getattr(profile, 'label', '')}".lower()
    if getattr(profile, "is_date", False):
        return False
    if not getattr(profile, "is_numeric", False):
        return False
    blocked_tokens = [" id", "_id", "code", "matricule", " ref", "reference", "date", "start", "end", "joining", "created"]
    return not any(token in name for token in blocked_tokens)


def create_chart_definition(
    dataset_doc,
    title,
    chart_type,
    value_field=None,
    category_field=None,
    calculation_type="Total",
    top_n=DEFAULT_TOP_N,
    order=1,
    secondary_value_field=None,
):
    warn_deprecated(
        "bi_studio.services.suggestion_engine.create_chart_definition",
        "bi_studio.api.dashboard_generator.generate_dashboard",
    )
    config = {
        "title": title,
        "chart_type": chart_type,
        "category_field": category_field,
        "value_field": value_field,
        "secondary_value_field": secondary_value_field,
        "calculation_type": calculation_type,
        "top_n": top_n,
    }
    data = get_chart_data(dataset_doc, config)
    doc = frappe.get_doc(
        {
            "doctype": "BI Chart Definition",
            "dataset": dataset_doc.name,
            "title": title,
            "chart_type": chart_type,
            "category_field": category_field,
            "value_field": value_field,
            "secondary_value_field": secondary_value_field,
            "calculation_type": calculation_type,
            "top_n": top_n,
            "display_order": order,
            "config_json": to_json(config),
            "data_preview_json": to_json(data),
        }
    )
    doc.insert(ignore_permissions=True)
    return doc


def generate_suggested_dashboard(dataset_doc, import_job=None):
    warn_deprecated(
        "bi_studio.services.suggestion_engine.generate_suggested_dashboard",
        "bi_studio.api.dashboard_generator.generate_dashboard",
    )
    kpis = frappe.get_all(
        "BI KPI Definition",
        filters={"dataset": dataset_doc.name},
        fields=["label", "source_column", "calculation_type", "display_order", "format_type"],
        order_by="display_order asc",
        limit=5,
    )
    charts = frappe.get_all(
        "BI Chart Definition",
        filters={"dataset": dataset_doc.name},
        fields=[
            "name",
            "title",
            "chart_type",
            "category_field",
            "value_field",
            "secondary_value_field",
            "calculation_type",
            "top_n",
            "display_order",
        ],
        order_by="display_order asc",
    )

    widgets = []
    for index, kpi in enumerate(kpis, start=1):
        widgets.append(
            {
                "id": f"kpi_{index}",
                "widget_type": "KPI",
                "title": kpi.label,
                "value_field": kpi.source_column,
                "calculation_type": kpi.calculation_type,
                "format_type": kpi.format_type or "Number",
                "order": index,
                "visible": True,
            }
        )

    for chart in charts:
        widgets.append(
            {
                "id": chart.name,
                "widget_type": "Chart",
                "title": chart.title,
                "chart_type": chart.chart_type,
                "category_field": chart.category_field,
                "value_field": chart.value_field,
                "secondary_value_field": chart.secondary_value_field,
                "calculation_type": chart.calculation_type,
                "top_n": chart.top_n or DEFAULT_TOP_N,
                "order": len(widgets) + 1,
                "visible": True,
            }
        )

    dashboard_name = unique_dashboard_name(f"Dashboard suggéré - {dataset_doc.dataset_name or dataset_doc.name}")
    dashboard = frappe.get_doc(
        {
            "doctype": "BI Dashboard",
            "dashboard_name": dashboard_name,
            "dataset": dataset_doc.name,
            "dashboard_type": "System Suggested",
            "is_system_suggested": 1,
            "is_user_created": 0,
            "generated_after_import": 1,
            "source_import_job": import_job.name if import_job else None,
            "widgets_json": to_json(widgets),
            "filters_json": "{}",
            "layout_json": to_json(default_layout(widgets)),
            "created_at": now_datetime(),
        }
    )
    for widget in widgets:
        dashboard.append("widgets", dashboard_widget_row(widget))
    dashboard.insert(ignore_permissions=True)

    dataset_doc.suggested_dashboard = dashboard.name
    dataset_doc.save(ignore_permissions=True)
    return dashboard


def dashboard_widget_row(widget):
    return {
        "widget_name": widget.get("title"),
        "widget_type": widget.get("widget_type"),
        "chart_type": widget.get("chart_type"),
        "value_field": widget.get("value_field"),
        "category_field": widget.get("category_field"),
        "calculation_type": widget.get("calculation_type"),
        "filters_json": to_json(widget.get("filters") or {}),
        "options_json": to_json(widget),
        "config_json": to_json(widget.get("config") or widget),
        "data_json": to_json(widget.get("data") or {}),
        "position_json": to_json(widget.get("position") or {}),
        "display_order": widget.get("order") or 1,
        "visible": int(widget.get("visible", True)),
    }


def default_layout(widgets):
    return {
        "kpis": [widget["id"] for widget in widgets if widget.get("widget_type") in {"KPI", "Quality Card"}][:5],
        "analysis_position": "after_kpis",
        "grid": [
            {"id": widget["id"], "width": 6, "height": 4}
            for widget in widgets
            if widget.get("widget_type") not in {"KPI", "Quality Card"}
        ],
    }


def unique_dashboard_name(base):
    name = base
    counter = 2
    while frappe.db.exists("BI Dashboard", {"dashboard_name": name}):
        name = f"{base} {counter}"
        counter += 1
    return name
