import frappe


ROLES = [
	"Wizio Administrator",
	"Wizio Business Manager",
	"Wizio Dashboard Manager",
	"Wizio User",
]


DEFAULT_WIDGETS = [
	{
		"widget_key": "kpi_count",
		"title": "KPI - Nombre de lignes",
		"widget_type": "KPI",
		"description": "Affiche un compteur sur le dataset lie.",
	},
	{
		"widget_key": "table_preview",
		"title": "Apercu tabulaire",
		"widget_type": "Table",
		"description": "Affiche les premieres lignes filtrees d'un dataset.",
	},
	{
		"widget_key": "ai_summary",
		"title": "Synthese IA",
		"widget_type": "AI Insight",
		"description": "Emplacement reserve aux interpretations automatiques configurees.",
	},
]


def after_install():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			doc = frappe.new_doc("Role")
			doc.role_name = role
			doc.desk_access = 1
			doc.insert(ignore_permissions=True)

	for widget in DEFAULT_WIDGETS:
		if frappe.db.exists("Wizio Widget", widget["widget_key"]):
			continue
		doc = frappe.new_doc("Wizio Widget")
		doc.widget_key = widget["widget_key"]
		doc.title = widget["title"]
		doc.widget_type = widget["widget_type"]
		doc.description = widget["description"]
		doc.is_active = 1
		for role in ["Wizio Administrator", "Wizio Business Manager", "Wizio Dashboard Manager"]:
			doc.append("roles", {"role": role, "can_view": 1, "can_use": 1})
		doc.insert(ignore_permissions=True)

	frappe.db.commit()
