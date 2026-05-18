"""Hooks d'installation BI Studio.

Aucune action automatique n'est requise: les rôles standards de Frappe
(System Manager) suffisent pour piloter le module BI Studio. Les anciens
rôles "Wizio *" et widgets prédéfinis (Wizio Widget) ne sont plus utilisés.
"""


def after_install() -> None:
	return None
