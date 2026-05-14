import frappe
from frappe import _
from frappe.utils import cint


ADMIN_ROLES = {"System Manager", "Wizio Administrator"}
BUSINESS_ROLES = {"Wizio Business Manager"}
DASHBOARD_ROLES = {"Wizio Dashboard Manager"}
USER_ROLES = {"Wizio User"}
ANALYSIS_ROLES = ADMIN_ROLES | BUSINESS_ROLES | DASHBOARD_ROLES
BUILDER_ROLES = ADMIN_ROLES | BUSINESS_ROLES | DASHBOARD_ROLES


def current_user(user: str | None = None) -> str:
	return user or frappe.session.user


def user_roles(user: str | None = None) -> set[str]:
	user = current_user(user)
	if user == "Administrator":
		return {"System Manager", "Wizio Administrator"}
	return set(frappe.get_roles(user))


def has_any_role(roles: set[str], user: str | None = None) -> bool:
	return bool(user_roles(user) & roles)


def is_admin(user: str | None = None) -> bool:
	return has_any_role(ADMIN_ROLES, user=user)


def require_authenticated() -> None:
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentification requise."), frappe.PermissionError)


def require_admin() -> None:
	require_authenticated()
	if not is_admin():
		frappe.throw(_("Action reservee a l'administrateur."), frappe.PermissionError)


def require_builder() -> None:
	require_authenticated()
	if not has_any_role(BUILDER_ROLES):
		frappe.throw(_("Vous n'avez pas acces a la gestion des tableaux de bord."), frappe.PermissionError)


def require_analysis_actor() -> None:
	require_authenticated()
	if not has_any_role(ANALYSIS_ROLES):
		frappe.throw(_("Vous n'avez pas acces aux analyses IA."), frappe.PermissionError)


def _owner_condition(doctype: str, user: str | None = None, owner_field: str = "owner") -> str | None:
	user = current_user(user)
	if is_admin(user):
		return None
	return f"`tab{doctype}`.`{owner_field}` = {frappe.db.escape(user)}"


def dataset_query(user: str | None = None) -> str | None:
	user = current_user(user)
	if is_admin(user) or has_any_role(BUSINESS_ROLES, user=user):
		return None
	return f"`tabWizio Dataset`.`imported_by` = {frappe.db.escape(user)}"


def dashboard_query(user: str | None = None) -> str | None:
	user = current_user(user)
	if is_admin(user):
		return None
	escaped = frappe.db.escape(user)
	return f"""
		(`tabWizio Dashboard`.`owner_user` = {escaped}
		or exists (
			select 1 from `tabWizio Dashboard Access`
			where `tabWizio Dashboard Access`.`parent` = `tabWizio Dashboard`.`name`
			and `tabWizio Dashboard Access`.`parenttype` = 'Wizio Dashboard'
			and `tabWizio Dashboard Access`.`user` = {escaped}
			and ifnull(`tabWizio Dashboard Access`.`can_read`, 0) = 1
		))
	"""


def widget_query(user: str | None = None) -> str | None:
	user = current_user(user)
	if is_admin(user):
		return None
	roles = [role for role in user_roles(user) if role not in {"All", "Guest"}]
	if not roles:
		return "1=0"
	role_list = ", ".join(frappe.db.escape(role) for role in roles)
	return f"""
		ifnull(`tabWizio Widget`.`is_active`, 0) = 1
		and exists (
			select 1 from `tabWizio Widget Role`
			where `tabWizio Widget Role`.`parent` = `tabWizio Widget`.`name`
			and `tabWizio Widget Role`.`parenttype` = 'Wizio Widget'
			and `tabWizio Widget Role`.`role` in ({role_list})
			and ifnull(`tabWizio Widget Role`.`can_view`, 0) = 1
		)
	"""


def analysis_query(user: str | None = None) -> str | None:
	return _owner_condition("Wizio Analysis", user=user, owner_field="generated_by")


def conversation_query(user: str | None = None) -> str | None:
	return _owner_condition("Wizio AI Conversation", user=user, owner_field="owner_user")


def message_query(user: str | None = None) -> str | None:
	user = current_user(user)
	if is_admin(user):
		return None
	escaped = frappe.db.escape(user)
	return f"""
		exists (
			select 1 from `tabWizio AI Conversation`
			where `tabWizio AI Conversation`.`name` = `tabWizio AI Message`.`conversation`
			and `tabWizio AI Conversation`.`owner_user` = {escaped}
		)
	"""


def can_read_dataset(dataset: str | object, user: str | None = None) -> bool:
	doc = frappe.get_doc("Wizio Dataset", dataset) if isinstance(dataset, str) else dataset
	user = current_user(user)
	return is_admin(user) or has_any_role(BUSINESS_ROLES, user=user) or doc.imported_by == user or doc.owner == user


def can_write_dataset(dataset: str | object, user: str | None = None) -> bool:
	doc = frappe.get_doc("Wizio Dataset", dataset) if isinstance(dataset, str) else dataset
	user = current_user(user)
	return is_admin(user) or has_any_role(BUSINESS_ROLES, user=user) or doc.imported_by == user


def assert_dataset_read(dataset: str):
	if not can_read_dataset(dataset):
		frappe.throw(_("Vous n'avez pas acces a ce dataset."), frappe.PermissionError)
	return frappe.get_doc("Wizio Dataset", dataset)


def assert_dataset_write(dataset: str):
	if not can_write_dataset(dataset):
		frappe.throw(_("Vous ne pouvez pas modifier ce dataset."), frappe.PermissionError)
	return frappe.get_doc("Wizio Dataset", dataset)


def _dashboard_access_flags(doc, user: str | None = None) -> dict[str, bool]:
	user = current_user(user)
	if is_admin(user):
		return {"read": True, "write": True}
	if doc.owner_user == user or doc.owner == user:
		return {"read": True, "write": True}
	flags = {"read": False, "write": False}
	for row in doc.get("access") or []:
		if row.user == user:
			flags["read"] = flags["read"] or bool(cint(row.can_read))
			flags["write"] = flags["write"] or bool(cint(row.can_write))
	return flags


def can_read_dashboard(dashboard: str | object, user: str | None = None) -> bool:
	doc = frappe.get_doc("Wizio Dashboard", dashboard) if isinstance(dashboard, str) else dashboard
	return _dashboard_access_flags(doc, user=user)["read"]


def can_write_dashboard(dashboard: str | object, user: str | None = None) -> bool:
	doc = frappe.get_doc("Wizio Dashboard", dashboard) if isinstance(dashboard, str) else dashboard
	return _dashboard_access_flags(doc, user=user)["write"]


def assert_dashboard_read(dashboard: str):
	if not can_read_dashboard(dashboard):
		frappe.throw(_("Vous n'avez pas acces a ce tableau de bord."), frappe.PermissionError)
	return frappe.get_doc("Wizio Dashboard", dashboard)


def assert_dashboard_write(dashboard: str):
	if not can_write_dashboard(dashboard):
		frappe.throw(_("Vous ne pouvez pas modifier ce tableau de bord."), frappe.PermissionError)
	return frappe.get_doc("Wizio Dashboard", dashboard)


def _widget_role_flags(widget_doc, user: str | None = None) -> dict[str, bool]:
	if is_admin(user):
		return {"view": True, "use": True}
	roles = user_roles(user)
	flags = {"view": False, "use": False}
	for row in widget_doc.get("roles") or []:
		if row.role in roles:
			flags["view"] = flags["view"] or bool(cint(row.can_view))
			flags["use"] = flags["use"] or bool(cint(row.can_use))
	return flags


def can_access_widget(widget: str | object, action: str = "view", user: str | None = None) -> bool:
	doc = frappe.get_doc("Wizio Widget", widget) if isinstance(widget, str) else widget
	if not cint(doc.is_active) and not is_admin(user):
		return False
	flags = _widget_role_flags(doc, user=user)
	return flags["use" if action == "use" else "view"]


def assert_widget_access(widget: str, action: str = "view"):
	if not can_access_widget(widget, action=action):
		frappe.throw(_("Widget indisponible ou non autorise."), frappe.PermissionError)
	return frappe.get_doc("Wizio Widget", widget)


def can_write_analysis(analysis: str | object, user: str | None = None) -> bool:
	doc = frappe.get_doc("Wizio Analysis", analysis) if isinstance(analysis, str) else analysis
	user = current_user(user)
	return is_admin(user) or doc.generated_by == user


def assert_analysis_write(analysis: str):
	if not can_write_analysis(analysis):
		frappe.throw(_("Vous ne pouvez pas modifier cette analyse."), frappe.PermissionError)
	return frappe.get_doc("Wizio Analysis", analysis)


def assert_conversation_read(conversation: str):
	doc = frappe.get_doc("Wizio AI Conversation", conversation)
	if not (is_admin() or doc.owner_user == frappe.session.user):
		frappe.throw(_("Vous n'avez pas acces a cette conversation."), frappe.PermissionError)
	return doc


def assert_conversation_write(conversation: str):
	doc = assert_conversation_read(conversation)
	if not (is_admin() or doc.owner_user == frappe.session.user):
		frappe.throw(_("Vous ne pouvez pas modifier cette conversation."), frappe.PermissionError)
	return doc


def has_dataset_permission(doc, ptype=None, user=None):
	if ptype in {"read", "report", "export", "print"}:
		return can_read_dataset(doc, user=user)
	if ptype in {"write", "delete", "create"}:
		return is_admin(user) or has_any_role(BUSINESS_ROLES, user=user) or getattr(doc, "imported_by", None) == current_user(user)
	return False


def has_dashboard_permission(doc, ptype=None, user=None):
	if ptype in {"read", "report", "export", "print"}:
		return can_read_dashboard(doc, user=user)
	if ptype in {"write", "delete"}:
		return can_write_dashboard(doc, user=user)
	if ptype == "create":
		return has_any_role(BUILDER_ROLES, user=user)
	return False


def has_widget_permission(doc, ptype=None, user=None):
	if ptype in {"read", "report", "export", "print"}:
		return can_access_widget(doc, action="view", user=user)
	if ptype in {"write", "delete", "create"}:
		return is_admin(user)
	return False


def has_analysis_permission(doc, ptype=None, user=None):
	if ptype in {"read", "report", "export", "print"}:
		return is_admin(user) or getattr(doc, "generated_by", None) == current_user(user) or has_any_role(ANALYSIS_ROLES, user=user)
	if ptype in {"write", "delete"}:
		return can_write_analysis(doc, user=user)
	if ptype == "create":
		return has_any_role(ANALYSIS_ROLES, user=user)
	return False


def has_conversation_permission(doc, ptype=None, user=None):
	if ptype in {"read", "report", "export", "print", "write", "delete"}:
		return is_admin(user) or getattr(doc, "owner_user", None) == current_user(user)
	if ptype == "create":
		return has_any_role(ANALYSIS_ROLES, user=user)
	return False


def has_message_permission(doc, ptype=None, user=None):
	if not getattr(doc, "conversation", None):
		return False
	conversation = frappe.get_doc("Wizio AI Conversation", doc.conversation)
	if ptype in {"read", "report", "export", "print", "write", "delete"}:
		return is_admin(user) or conversation.owner_user == current_user(user)
	if ptype == "create":
		return has_any_role(ANALYSIS_ROLES, user=user)
	return False
