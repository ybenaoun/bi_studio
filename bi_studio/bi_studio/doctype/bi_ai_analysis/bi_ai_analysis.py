import frappe
from frappe import _
from frappe.model.document import Document


class BIAIAnalysis(Document):
    def validate(self):
        if not self.dashboard:
            frappe.throw(_("Une analyse IA doit toujours être reliée à un dashboard."))

    def on_trash(self):
        for favorite in frappe.get_all(
            "BI Favorite",
            filters={"reference_doctype": "BI AI Analysis", "reference_name": self.name},
            pluck="name",
        ):
            frappe.delete_doc("BI Favorite", favorite, ignore_permissions=True, force=True)

