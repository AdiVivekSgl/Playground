# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Installation / setup routine (after_migrate).

Adds the five Google Document metadata custom fields to every DocType that has
an enabled `Google Document Template`, so the field mechanism is reusable and
never requires hand-editing standard ERPNext DocType JSON (spec 8). Idempotent:
`create_custom_fields` upserts, so it is safe to re-run on every migrate, and it
is also called from the template controller's on_update so enabling a new
DocType provisions its fields immediately.
"""

import frappe

from playground.playground.google_docs.constants import META_FIELDS, STATUS_OPTIONS

_SECTION = "custom_google_document_section"


def _field_defs():
	"""The metadata fields, grouped under a collapsible 'Google Document' section."""
	options = "\n".join(STATUS_OPTIONS)
	return [
		{
			"fieldname": _SECTION,
			"label": "Google Document",
			"fieldtype": "Section Break",
			"collapsible": 1,
			"insert_after": None,  # appended at the end of the form
		},
		{
			"fieldname": META_FIELDS["status"],
			"label": "Google Document Status",
			"fieldtype": "Select",
			"options": options,
			"default": STATUS_OPTIONS[0],
			"read_only": 1,
			"no_copy": 1,
			"insert_after": _SECTION,
			"allow_on_submit": 1,
		},
		{
			"fieldname": META_FIELDS["url"],
			"label": "Google Document URL",
			"fieldtype": "Data",
			"options": "URL",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": META_FIELDS["status"],
			"allow_on_submit": 1,
		},
		{
			"fieldname": META_FIELDS["id"],
			"label": "Google Document ID",
			"fieldtype": "Data",
			"read_only": 1,
			"no_copy": 1,
			"hidden": 1,
			"insert_after": META_FIELDS["url"],
			"allow_on_submit": 1,
		},
		{
			"fieldname": META_FIELDS["created_on"],
			"label": "Google Document Created On",
			"fieldtype": "Datetime",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": META_FIELDS["id"],
			"allow_on_submit": 1,
		},
		{
			"fieldname": META_FIELDS["last_updated"],
			"label": "Google Document Last Updated",
			"fieldtype": "Datetime",
			"read_only": 1,
			"no_copy": 1,
			"insert_after": META_FIELDS["created_on"],
			"allow_on_submit": 1,
		},
	]


def ensure_metadata_fields(reference_doctype):
	"""Create/refresh the metadata custom fields on one reference DocType."""
	if not reference_doctype:
		return
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	create_custom_fields({reference_doctype: _field_defs()}, ignore_validate=True)


def enabled_reference_doctypes():
	rows = frappe.get_all(
		"Google Document Template",
		filters={"enabled": 1},
		fields=["distinct reference_doctype as dt"],
	)
	return [r.dt for r in rows if r.dt]


def setup_google_docs():
	"""after_migrate hook — provision metadata fields for every enabled template.

	Defensive: a failure here must never abort `bench migrate` (on Frappe Cloud a
	failed migrate rolls the whole DB back). It simply re-runs next migrate.
	"""
	try:
		for dt in enabled_reference_doctypes():
			ensure_metadata_fields(dt)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "setup_google_docs failed")
