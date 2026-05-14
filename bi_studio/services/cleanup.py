import frappe

from bi_studio.services.warehouse import drop_dataset_tables


def delete_doc_if_exists(doctype, name):
    if name and frappe.db.exists(doctype, name):
        frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)


def delete_favorites(reference_doctype, reference_name):
    for favorite in frappe.get_all(
        "BI Favorite",
        filters={"reference_doctype": reference_doctype, "reference_name": reference_name},
        pluck="name",
    ):
        delete_doc_if_exists("BI Favorite", favorite)


def delete_ai_analysis_cascade(analysis_name):
    delete_favorites("BI AI Analysis", analysis_name)
    delete_doc_if_exists("BI AI Analysis", analysis_name)


def delete_dashboard_cascade(dashboard_name):
    if not frappe.db.exists("BI Dashboard", dashboard_name):
        return

    for analysis in frappe.get_all("BI AI Analysis", filters={"dashboard": dashboard_name}, pluck="name"):
        delete_ai_analysis_cascade(analysis)

    delete_favorites("BI Dashboard", dashboard_name)

    for dataset in frappe.get_all(
        "BI Dataset",
        filters={"suggested_dashboard": dashboard_name},
        pluck="name",
    ):
        frappe.db.set_value("BI Dataset", dataset, "suggested_dashboard", None, update_modified=False)

    delete_doc_if_exists("BI Dashboard", dashboard_name)


def delete_dataset_cascade(dataset_name):
    if not frappe.db.exists("BI Dataset", dataset_name):
        return

    dataset = frappe.get_doc("BI Dataset", dataset_name)
    dashboards = frappe.get_all("BI Dashboard", filters={"dataset": dataset.name}, pluck="name")

    for analysis in frappe.get_all("BI AI Analysis", filters={"dataset": dataset.name}, pluck="name"):
        delete_ai_analysis_cascade(analysis)

    for dashboard in dashboards:
        delete_dashboard_cascade(dashboard)

    delete_favorites("BI Dataset", dataset.name)
    drop_dataset_tables(dataset)

    for doctype in ["BI KPI Definition", "BI Chart Definition", "BI Warehouse Model", "BI Fact Table", "BI Dimension Table"]:
        for name in frappe.get_all(doctype, filters={"dataset": dataset.name}, pluck="name"):
            delete_doc_if_exists(doctype, name)

    for import_job in frappe.get_all("BI Import Job", filters={"dataset": dataset.name}, pluck="name"):
        frappe.db.set_value("BI Import Job", import_job, "dataset", None, update_modified=False)

    frappe.db.set_value(
        "BI Dataset",
        dataset.name,
        {"import_job": None, "suggested_dashboard": None},
        update_modified=False,
    )
    delete_doc_if_exists("BI Dataset", dataset.name)
