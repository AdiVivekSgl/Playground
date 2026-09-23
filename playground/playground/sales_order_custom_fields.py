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
	try:
		_create_pending_fields()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "setup_sales_order_pending_fields failed")


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


def _create_pending_fields():
	"""Pending Qty / Pending Amount columns on the Sales Order items table.

	Both are VIRTUAL (no DB column): delivered_qty is bumped by Delivery Note
	submit/cancel via a direct db update that never re-runs the Sales Order's
	validate, so a stored "pending" column would silently go stale. Instead the
	values are computed on the fly in the form (playground/public/js/sales_order.js)
	from qty - delivered_qty, so they are always current when the order is opened.

	Pending Amount is a Float rather than Currency on purpose: a virtual Currency
	field's `options` (the currency fieldname) can be misread as a virtual-field
	expression; the form shows the order's currency on the header totals instead.
	"""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	fields = [
		{
			"fieldname": "custom_pending_qty",
			"label": "Pending Qty",
			"fieldtype": "Float",
			"insert_after": "delivered_qty",
			"is_virtual": 1,
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"columns": 1,
			"description": "Ordered qty not yet delivered (qty - delivered qty).",
		},
		{
			"fieldname": "custom_pending_amount",
			"label": "Pending Amount",
			"fieldtype": "Float",
			"insert_after": "custom_pending_qty",
			"is_virtual": 1,
			"read_only": 1,
			"no_copy": 1,
			"in_list_view": 1,
			"columns": 1,
			"description": "Pending qty x rate, in the Sales Order currency.",
		},
	]
	create_custom_fields({"Sales Order Item": fields}, ignore_validate=True)
