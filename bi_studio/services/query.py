import math

import frappe
from frappe import _

from bi_studio.config import DEFAULT_TOP_N, SUPPORTED_CALCULATIONS
from bi_studio.services.naming import quote_identifier
from bi_studio.services.serialization import from_json
from bi_studio.services.warehouse import ensure_clean_table_exists


def get_dataset_columns(dataset_name):
    columns = frappe.get_all(
        "BI Dataset Column",
        filters={"parent": dataset_name, "parenttype": "BI Dataset"},
        fields=[
            "column_label",
            "column_name",
            "data_type",
            "detected_type",
            "semantic_role",
            "aggregation",
            "include_in_analysis",
            "is_numeric",
            "is_category",
            "is_date",
            "distinct_count",
            "null_count",
            "null_rate",
            "sample_values_json",
            "warnings",
            "display_order",
        ],
        order_by="display_order asc",
    )
    for column in columns:
        column["sample_values"] = from_json(column.pop("sample_values_json", None), [])
        column["warnings"] = from_json(column.get("warnings"), [])
    return columns


def validate_column(dataset_doc, column_name, allow_empty=False):
    if allow_empty and not column_name:
        return None
    allowed = {column["column_name"] for column in get_dataset_columns(dataset_doc.name)}
    if column_name not in allowed:
        frappe.throw(_("Champ invalide pour ce dataset."))
    return column_name


def calculation_sql(calculation_type, value_field):
    calculation = SUPPORTED_CALCULATIONS.get(calculation_type or "Total", "SUM")
    if calculation == "COUNT":
        return "COUNT(*)" if not value_field else f"COUNT({quote_identifier(value_field)})"
    if calculation == "COUNT DISTINCT":
        return f"COUNT(DISTINCT {quote_identifier(value_field)})"
    return f"{calculation}({quote_identifier(value_field)})"


def get_kpi_value(dataset_doc, widget):
    if widget.get("value") is not None and not widget.get("value_field"):
        return widget.get("value")
    ensure_clean_table_exists(dataset_doc)
    value_field = widget.get("value_field") or widget.get("value")
    calculation_type = widget.get("calculation_type") or "Total"
    if calculation_type not in {"Nombre"}:
        validate_column(dataset_doc, value_field)
        validate_calculation_allowed(dataset_doc, value_field, calculation_type)
    agg = calculation_sql(calculation_type, value_field)
    result = frappe.db.sql(
        f"SELECT {agg} AS value FROM {quote_identifier(dataset_doc.clean_table)}",
        as_dict=True,
    )[0]
    return result.value or 0


def get_chart_data(dataset_doc, widget):
    ensure_clean_table_exists(dataset_doc)
    chart_type = widget.get("chart_type") or widget.get("type") or "Bar"
    if chart_type == "Histogram":
        return get_histogram_data(dataset_doc, widget)
    if chart_type in {"Scatter", "Scatter Plot"}:
        return get_scatter_data(dataset_doc, widget)
    if chart_type == "KPI Card":
        return {"value": get_kpi_value(dataset_doc, widget)}

    category_field = widget.get("category_field")
    value_field = widget.get("value_field")
    calculation_type = widget.get("calculation_type") or "Total"
    top_n = int(widget.get("top_n") or DEFAULT_TOP_N)

    validate_column(dataset_doc, category_field)
    if calculation_type != "Nombre":
        validate_column(dataset_doc, value_field)
        validate_calculation_allowed(dataset_doc, value_field, calculation_type)

    agg = calculation_sql(calculation_type, value_field)
    order_clause = (
        f"ORDER BY {quote_identifier(category_field)} ASC"
        if _is_date_column(dataset_doc.name, category_field)
        else "ORDER BY value DESC"
    )
    rows = frappe.db.sql(
        f"""
        SELECT {quote_identifier(category_field)} AS label, {agg} AS value
        FROM {quote_identifier(dataset_doc.clean_table)}
        WHERE {quote_identifier(category_field)} IS NOT NULL
        GROUP BY {quote_identifier(category_field)}
        {order_clause}
        LIMIT %s
        """,
        (top_n,),
        as_dict=True,
    )
    return {"labels": [str(row.label) for row in rows], "values": [float(row.value or 0) for row in rows]}


def get_histogram_data(dataset_doc, widget):
    ensure_clean_table_exists(dataset_doc)
    value_field = widget.get("value_field")
    validate_column(dataset_doc, value_field)
    validate_measure_column(dataset_doc, value_field)
    rows = frappe.db.sql(
        f"SELECT {quote_identifier(value_field)} AS value FROM {quote_identifier(dataset_doc.clean_table)} "
        f"WHERE {quote_identifier(value_field)} IS NOT NULL",
        as_dict=True,
    )
    values = [float(row.value) for row in rows if row.value is not None]
    if not values:
        return {"labels": [], "values": []}

    bucket_count = min(10, max(4, int(math.sqrt(len(values)))))
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        return {"labels": [str(minimum)], "values": [len(values)]}

    width = (maximum - minimum) / bucket_count
    buckets = [0] * bucket_count
    for value in values:
        index = min(bucket_count - 1, int((value - minimum) / width))
        buckets[index] += 1

    labels = [f"{minimum + i * width:.2f} - {minimum + (i + 1) * width:.2f}" for i in range(bucket_count)]
    return {"labels": labels, "values": buckets}


def get_scatter_data(dataset_doc, widget):
    ensure_clean_table_exists(dataset_doc)
    x_field = widget.get("value_field")
    y_field = widget.get("secondary_value_field")
    validate_column(dataset_doc, x_field)
    validate_column(dataset_doc, y_field)
    validate_measure_column(dataset_doc, x_field)
    validate_measure_column(dataset_doc, y_field)
    rows = frappe.db.sql(
        f"""
        SELECT {quote_identifier(x_field)} AS x, {quote_identifier(y_field)} AS y
        FROM {quote_identifier(dataset_doc.clean_table)}
        WHERE {quote_identifier(x_field)} IS NOT NULL AND {quote_identifier(y_field)} IS NOT NULL
        LIMIT 500
        """,
        as_dict=True,
    )
    points = [{"x": float(row.x), "y": float(row.y)} for row in rows if row.x is not None and row.y is not None]
    return {"points": points, "labels": [str(index + 1) for index in range(len(points))], "values": [point["y"] for point in points]}


def _is_date_column(dataset_name, column_name):
    return bool(
        frappe.db.get_value(
            "BI Dataset Column",
            {"parent": dataset_name, "column_name": column_name, "is_date": 1},
            "name",
        )
    )


def get_column_meta(dataset_name, column_name):
    if not column_name:
        return None
    for column in get_dataset_columns(dataset_name):
        if column.column_name == column_name:
            return column
    return None


def validate_calculation_allowed(dataset_doc, column_name, calculation_type):
    meta = get_column_meta(dataset_doc.name, column_name)
    if not meta:
        frappe.throw(_("Champ invalide pour ce dataset."))
    semantic_role = meta.get("semantic_role") or infer_legacy_role(meta)
    data_type = meta.get("detected_type") or meta.get("data_type")
    numeric_aggregations = {"Total", "Moyenne", "Minimum", "Maximum"}
    if calculation_type in {"Nombre", "Nombre unique"}:
        return
    if calculation_type in numeric_aggregations and semantic_role != "Measure":
        frappe.throw(_("Agrégation non autorisée: {0} n'est pas une mesure.").format(meta.column_label))
    if calculation_type in {"Total", "Moyenne"} and data_type == "Date":
        frappe.throw(_("Les dates ne peuvent pas être additionnées ou moyennées."))
    if semantic_role == "Identifier" and calculation_type != "Nombre unique":
        frappe.throw(_("Les identifiants ne peuvent pas être additionnés."))


def validate_measure_column(dataset_doc, column_name):
    meta = get_column_meta(dataset_doc.name, column_name)
    if not meta:
        frappe.throw(_("Champ invalide pour ce dataset."))
    semantic_role = meta.get("semantic_role") or infer_legacy_role(meta)
    if semantic_role != "Measure":
        frappe.throw(_("Ce graphique nécessite une mesure numérique: {0}.").format(meta.column_label))


def infer_legacy_role(meta):
    name = f"{meta.get('column_name')} {meta.get('column_label')}".lower()
    if meta.get("is_date") or any(token in name for token in ["date", "start", "end", "joining", "created"]):
        return "Date Dimension"
    if any(token in name for token in [" id", "_id", "code", "matricule", " ref"]):
        return "Identifier"
    if meta.get("is_numeric"):
        return "Measure"
    if meta.get("is_category"):
        return "Dimension"
    return "Attribute"


def get_clean_rows(dataset_doc, start=0, page_length=50, search=None, order_by=None, order_dir="asc"):
    ensure_clean_table_exists(dataset_doc)
    columns = get_dataset_columns(dataset_doc.name)
    fields = [column["column_name"] for column in columns]
    if not fields:
        return {"columns": columns, "rows": [], "total": 0}
    where = ""
    params = []
    if search:
        clauses = [f"CAST({quote_identifier(field)} AS CHAR) LIKE %s" for field in fields]
        where = "WHERE " + " OR ".join(clauses)
        params.extend([f"%{search}%"] * len(fields))

    order = ""
    if order_by in fields:
        direction = "DESC" if str(order_dir).lower() == "desc" else "ASC"
        order = f"ORDER BY {quote_identifier(order_by)} {direction}"

    rows = frappe.db.sql(
        f"SELECT {', '.join(quote_identifier(field) for field in fields)} "
        f"FROM {quote_identifier(dataset_doc.clean_table)} {where} {order} LIMIT %s OFFSET %s",
        (*params, int(page_length), int(start)),
        as_dict=True,
    )
    total = frappe.db.sql(f"SELECT COUNT(*) AS total FROM {quote_identifier(dataset_doc.clean_table)}", as_dict=True)[0].total
    return {"columns": columns, "rows": rows, "total": total}
