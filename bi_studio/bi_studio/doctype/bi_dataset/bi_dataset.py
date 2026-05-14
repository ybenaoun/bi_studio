import frappe
from frappe.model.document import Document

from bi_studio.services.warehouse import drop_dataset_tables


class BIDataset(Document):
    def validate(self):
        if self.is_new() and not self.status:
            self.status = "Draft"

    def on_trash(self):
        related_dashboards = frappe.get_all("BI Dashboard", filters={"dataset": self.name}, pluck="name")
        related_analyses = frappe.get_all("BI AI Analysis", filters={"dataset": self.name}, pluck="name")
        related_favorites = frappe.get_all(
            "BI Favorite",
            filters={"reference_doctype": "BI Dataset", "reference_name": self.name},
            pluck="name",
        )

        for dashboard in related_dashboards:
            frappe.delete_doc("BI Dashboard", dashboard, ignore_permissions=True, force=True)
        for analysis in related_analyses:
            if frappe.db.exists("BI AI Analysis", analysis):
                frappe.delete_doc("BI AI Analysis", analysis, ignore_permissions=True, force=True)
        for favorite in related_favorites:
            frappe.delete_doc("BI Favorite", favorite, ignore_permissions=True, force=True)

        drop_dataset_tables(self)

        for doctype in ["BI KPI Definition", "BI Chart Definition", "BI Warehouse Model", "BI Fact Table", "BI Dimension Table"]:
            for name in frappe.get_all(doctype, filters={"dataset": self.name}, pluck="name"):
                frappe.delete_doc(doctype, name, ignore_permissions=True, force=True)
