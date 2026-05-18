"""Permissions et garde-fous pour le module BI Studio.

Cette couche s'appuie sur les permissions Frappe standards déclarées dans les
doctypes BI* (System Manager + "User du wizio"). Elle expose des helpers
ensure_* qui vérifient à la fois l'authentification, le rôle et l'accès au
document, et renvoient le document chargé pour les appelants.
"""

from __future__ import annotations

import frappe
from frappe import _


ADMIN_ROLES = {"System Manager", "Administrator"}


def current_user(user: str | None = None) -> str:
	return user or frappe.session.user


def user_roles(user: str | None = None) -> set[str]:
	user = current_user(user)
	if user == "Administrator":
		return {"System Manager", "Administrator"}
	return set(frappe.get_roles(user))


def is_admin(user: str | None = None) -> bool:
	user = current_user(user)
	if user == "Administrator":
		return True
	return bool(user_roles(user) & ADMIN_ROLES)


def ensure_authenticated() -> str:
	"""Lève PermissionError si l'utilisateur courant est Guest. Renvoie le user."""
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Authentification requise."), frappe.PermissionError)
	return user


def ensure_admin() -> None:
	ensure_authenticated()
	if not is_admin():
		frappe.throw(_("Action réservée aux administrateurs."), frappe.PermissionError)


def _ensure_doc_access(doctype: str, name: str, ptype: str = "read"):
	ensure_authenticated()
	if not name:
		frappe.throw(_("Identifiant manquant."))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("{0} introuvable: {1}").format(_(doctype), name), frappe.DoesNotExistError)
	if not is_admin() and not frappe.has_permission(doctype, ptype=ptype, doc=name):
		frappe.throw(_("Accès refusé sur {0}.").format(_(doctype)), frappe.PermissionError)
	return frappe.get_doc(doctype, name)


def ensure_dataset_access(dataset_name: str, ptype: str = "read"):
	return _ensure_doc_access("BI Dataset", dataset_name, ptype=ptype)


def ensure_dashboard_access(dashboard_name: str, ptype: str = "read"):
	return _ensure_doc_access("BI Dashboard", dashboard_name, ptype=ptype)


def ensure_ai_analysis_access(analysis_name: str, ptype: str = "read"):
	return _ensure_doc_access("BI AI Analysis", analysis_name, ptype=ptype)
