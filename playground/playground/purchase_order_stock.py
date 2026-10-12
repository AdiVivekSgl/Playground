# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Order — live stock columns
===================================

ERPNext fills the Purchase Order Item stock columns (`actual_qty` "Available Qty
at Warehouse", `company_total_stock` "Available Qty at Company", `projected_qty`)
only when an item/warehouse is picked, so a draft PO opened days later still shows
the stock of the moment the line was added.

This refreshes them from the Bins:
  - onload   : every time a DRAFT PO is opened / reloaded, the values sent to the
               form are recomputed in memory (nothing is written; the next save
               persists them).
  - validate : on save and on submit, so the stored values are current as of the
               last save / the moment of submission.

Submitted POs are left alone: their stored values are the snapshot at submission,
and changing them in memory would trip "Not allowed to change ... after
submission" on any later Update-After-Submit save.

Numbers come from ERPNext's own get_bin_details() — the same helper that fills the
columns when an item is selected — so they match the native behaviour exactly.
"""

import frappe
from erpnext.stock.get_item_details import get_bin_details

STOCK_FIELDS = ("actual_qty", "company_total_stock", "projected_qty")


def on_purchase_order_onload(doc, method=None):
	if doc.docstatus == 0:
		refresh_item_stock(doc)


def on_purchase_order_validate(doc, method=None):
	# docstatus is already 1 during submit's validate; only update-after-submit
	# (which must not touch these fields) is excluded.
	if doc.docstatus == 0 or getattr(doc, "_action", None) == "submit":
		refresh_item_stock(doc)


def refresh_item_stock(doc):
	cache = {}
	for row in doc.get("items") or []:
		if not row.item_code:
			continue
		warehouse = row.warehouse or doc.get("set_warehouse")
		key = (row.item_code, warehouse)
		if key not in cache:
			cache[key] = get_bin_details(row.item_code, warehouse, company=doc.company)
		details = cache[key]
		for field in STOCK_FIELDS:
			if field in details and row.meta.has_field(field):
				row.set(field, details[field])
