# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Item Reservations and Demand
============================

For a single Item, a unified list of what's committed against it and what's
demanding it, in two parts (UNION):

  Reserved  - active (docstatus 1) Stock Reservation Entries for the item, with
              the reserving Sales Order / Work Order, its reference (customer /
              production item) and status, and the reserved qty.
  Demand    - open Material Request lines for the item (submitted, with
              qty - ordered_qty > 0), i.e. outstanding requested quantity.

The "Document" column is a Dynamic Link resolved by the "Document Type" column,
so each row links straight to its Sales Order / Work Order / Material Request.

Requires the `item_code` filter.
"""

import frappe
from frappe import _


def execute(filters=None):
	filters = filters or {}
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Data", "width": 130},
		# Dynamic Link resolved by the row's Document Type (Sales Order / Work
		# Order / Material Request), so the value links to the right doctype.
		{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "document_type", "width": 170},
		{"label": _("Reference"), "fieldname": "reference", "fieldtype": "Data", "width": 180},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 120},
		{"label": _("Reservation Type"), "fieldname": "reservation_type", "fieldtype": "Data", "width": 120},
	]


def get_data(filters):
	item_code = filters.get("item_code")
	if not item_code:
		return []

	return frappe.db.sql(
		"""
		SELECT
			sre.item_code AS item_code,
			sre.warehouse AS warehouse,
			sre.voucher_type AS document_type,
			sre.voucher_no AS document,
			CASE
				WHEN sre.voucher_type = 'Sales Order' THEN so.customer
				WHEN sre.voucher_type = 'Work Order' THEN wo.production_item
				ELSE ''
			END AS reference,
			CASE
				WHEN sre.voucher_type = 'Sales Order' THEN so.status
				WHEN sre.voucher_type = 'Work Order' THEN wo.status
				ELSE ''
			END AS status,
			sre.reserved_qty AS qty,
			'Reserved' AS reservation_type
		FROM `tabStock Reservation Entry` sre
		LEFT JOIN `tabSales Order` so
			ON so.name = sre.voucher_no AND sre.voucher_type = 'Sales Order'
		LEFT JOIN `tabWork Order` wo
			ON wo.name = sre.voucher_no AND sre.voucher_type = 'Work Order'
		WHERE sre.docstatus = 1
			AND sre.item_code = %(item_code)s

		UNION ALL

		SELECT
			mri.item_code AS item_code,
			mri.warehouse AS warehouse,
			'Material Request' AS document_type,
			mr.name AS document,
			mr.transaction_date AS reference,
			mr.status AS status,
			(mri.qty - mri.ordered_qty) AS qty,
			'Demand' AS reservation_type
		FROM `tabMaterial Request Item` mri
		INNER JOIN `tabMaterial Request` mr
			ON mr.name = mri.parent
		WHERE mr.docstatus = 1
			AND mri.item_code = %(item_code)s
			AND (mri.qty - mri.ordered_qty) > 0

		ORDER BY item_code, warehouse, document_type
		""",
		{"item_code": item_code},
		as_dict=True,
	)
