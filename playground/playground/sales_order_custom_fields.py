# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Sales Order — Ultimate Owner field
==================================

Adds an editable "Ultimate Owner" Data field just below Customer on the Sales
Order. It pre-fills with the customer's name (fetch_from customer.customer_name,
with fetch_if_empty) so the ultimate owner DEFAULTS to the customer, but stays
freely editable so a different owning entity — e.g. a parent / holding company —
can be recorded instead.

Why fetch_if_empty rather than a static Default: a plain "Default" property can
only hold a fixed string, and would put the same literal text on every order. The
requirement is that each order defaults to ITS OWN customer's name, which is a
per-record value; fetch_from pulls customer.customer_name, and fetch_if_empty
means it populates only while the field is blank — so a manual override is never
clobbered when the doc is re-saved.

Created idempotently in after_migrate (create_custom_fields upserts), so the field
travels with the app on `bench migrate` rather than being a one-off Customize Form
change. Wrapped defensively: a failure here must never abort migrate (on Frappe
Cloud a failed migrate rolls the whole DB back); it re-runs on the next migrate.
"""

import frappe


def setup_sales_order_custom_fields():
	"""after_migrate hook — create/refresh the Sales Order custom fields."""
	try:
		_create_ultimate_owner_field()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "setup_sales_order_custom_fields failed")


def _create_ultimate_owner_field():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	fields = [
		{
			"fieldname": "custom_ultimate_owner",
			"label": "Ultimate Owner",
			"fieldtype": "Data",
			# Just below the Customer link field (which is followed by customer_name).
			"insert_after": "customer",
			# Default to the customer's name, but only while the field is empty, so a
			# manual override to a parent / holding company is preserved on re-save.
			"fetch_from": "customer.customer_name",
			"fetch_if_empty": 1,
			"description": (
				"Ultimate owning entity behind this order. Defaults to the customer's "
				"name; edit to record a different owner (e.g. a parent company)."
			),
		},
	]
	create_custom_fields({"Sales Order": fields}, ignore_validate=True)
