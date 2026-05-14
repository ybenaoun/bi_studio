import frappe
from frappe.model.document import Document


class BIDashboard(Document):
    def validate(self):
        if self.dashboard_type == "System Suggested":
            self.is_system_suggested = 1
            self.is_user_created = 0
        elif self.dashboard_type == "User Created":
            self.is_user_created = 1
            self.is_system_suggested = 0

    def on_trash(self):
        for analysis in frappe.get_all("BI AI Analysis", filters={"dashboard": self.name}, pluck="name"):
            frappe.delete_doc("BI AI Analysis", analysis, ignore_permissions=True, force=True)
        for favorite in frappe.get_all(
            "BI Favorite",
            filters={"reference_doctype": "BI Dashboard", "reference_name": self.name},
            pluck="name",
        ):
            frappe.delete_doc("BI Favorite", favorite, ignore_permissions=True, force=True)

