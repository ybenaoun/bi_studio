import frappe
from frappe import _
from frappe.model.document import Document


class BIFavorite(Document):
    def validate(self):
        if self.reference_doctype not in {"BI Dataset", "BI Dashboard", "BI AI Analysis"}:
            frappe.throw(_("Type de favori non supporté."))
        existing = frappe.db.get_value(
            "BI Favorite",
            {
                "user": self.user,
                "reference_doctype": self.reference_doctype,
                "reference_name": self.reference_name,
                "name": ["!=", self.name],
            },
            "name",
        )
        if existing:
            frappe.throw(_("Cet élément est déjà dans les favoris."))

