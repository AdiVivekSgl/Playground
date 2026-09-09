# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Permission helpers.

Frappe permissions are authoritative: a user may only create/update/open a
Google Doc for an ERP document they can already read (and, for writes, write).
Google *sharing* of the generated file is a separate, configurable concern
handled in drive.apply_sharing.
"""

import frappe

from playground.playground.google_docs.exceptions import GoogleDocsError


def check_erp_permission(doctype, name, ptype="read"):
	"""Raise unless the session user has `ptype` on the ERP document.

	`frappe.has_permission` applies role permissions, user permissions and any
	permission query conditions, so this respects everything the desk would.
	"""
	if not frappe.has_permission(doctype=doctype, ptype=ptype, doc=name):
		raise frappe.PermissionError(
			frappe._("You are not permitted to {0} {1} {2}.").format(ptype, doctype, name)
		)


def get_document(doctype, name, ptype="read"):
	"""Permission-checked fetch of the source ERP document."""
	check_erp_permission(doctype, name, ptype=ptype)
	return frappe.get_doc(doctype, name)


def sharing_domain():
	"""The Workspace domain used for 'Company (Domain)' sharing.

	Derived from the impersonation user on Settings, else the site's default
	outgoing email domain; None if neither is available.
	"""
	from playground.playground.google_docs.auth import get_settings

	try:
		settings = get_settings()
	except GoogleDocsError:
		return None
	user = (settings.impersonate_user or "").strip()
	if "@" in user:
		return user.split("@")[-1]
	return None
