# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
FG Stock Reservation Manager
============================

A central screen to review, create, and delete Stock Reservation Entries (SREs)
against Sales Orders for finished goods, all in one warehouse
(STOCK_WAREHOUSE). One row per open Sales Order item line:

  Pending Qty     = qty - delivered_qty
  Reserved Qty    = SRE reserved_qty for THIS SO item line in STOCK_WAREHOUSE
  Item Free Stock = Bin.actual_qty - Bin.reserved_qty in STOCK_WAREHOUSE (item-level)
  Reservable Now  = min(Pending - Reserved, Item Free Stock)
  Reserve Qty     = editable; how much to reserve now (defaults to Reservable Now)

Actions (see the client script):
  - Create Reservations: reserve the entered qty per line, capped at free stock
    (FIFO by delivery date across lines sharing an item), via ERPNext's native
    reservation path.
  - Cancel Reservations: cancel the SREs on the selected lines, releasing stock.

Prerequisite: "Stock Reservation" must be enabled in Stock Settings.

Heavy reuse of the Production Requirement Report helpers so the two screens stay
consistent.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate
from datetime import date

from playground.playground.report.production_requirement_report.production_requirement_report import (
	STOCK_WAREHOUSE,
	get_open_so_items,
	get_stock_map,
	get_item_map,
	_get_so_header_map,
	_resolve_date_field,
)


def execute(filters=None):
	filters = filters or {}

	so_items = get_open_so_items(filters)
	if not so_items:
		return get_columns(), []

	fg_items = sorted(set(r.item_code for r in so_items))
	sos = sorted(set(r.sales_order for r in so_items))
	line_reserved = get_line_reserved_map([r.so_item for r in so_items])
	stock_map = get_stock_map(fg_items)
	item_map = get_item_map(fg_items)

	# "Date" column shows the SO date chosen by the Date Basis dropdown.
	so_date_map = _get_so_date_map(sos, filters.get("date_basis"))

	# Item free stock is shared across an item's SO lines; deduct as we walk the
	# lines (FIFO by delivery date) so "Reservable Qty" doesn't over-promise the
	# same units to two lines.
	so_header = _get_so_header_map(sos)

	def _sort_key(r):
		sd = so_header.get(r.sales_order, {}).get("sort_date")
		return (getdate(sd) if sd else date.max, r.sales_order, r.item_code)

	free_left = {}
	for item in fg_items:
		stock = stock_map.get(item) or frappe._dict()
		free_left[item] = flt(stock.get("actual_qty")) - flt(stock.get("reserved_qty"))

	only_unreserved = cint(filters.get("only_unreserved"))

	data = []
	for r in sorted(so_items, key=_sort_key):
		details = item_map.get(r.item_code) or frappe._dict()
		res = line_reserved.get(r.so_item) or frappe._dict()
		reserved = flt(res.get("reserved_qty"))
		pending = flt(r.pending_qty)
		outstanding = pending - reserved

		item_free = max(0.0, free_left.get(r.item_code, 0.0))
		reservable = max(0.0, min(outstanding, item_free))
		# Reserve against this line consumes shared item stock for later lines.
		free_left[r.item_code] = item_free - reservable

		if only_unreserved and outstanding <= 0:
			continue

		# Short to Complete = Pending − Reserved (unfilled demand for this SO
		# line) — the same pending−reserved figure the PRR nets before it
		# subtracts free stock/buffer.
		short_to_complete = max(0.0, outstanding)

		data.append(
			{
				"item_code": r.item_code,
				"item_name": details.get("item_name"),
				"customer": r.customer,
				"sales_order": r.sales_order,
				"so_date": so_date_map.get(r.sales_order),
				"sales_order_item": r.so_item,
				"pending_qty": pending,
				"reserved_qty": reserved,
				"short_to_complete": short_to_complete,
				"reservable_now": reservable,
				"reserve_qty": reservable,
				"existing_sre": ",".join(res.get("sre_names") or []),
			}
		)

	return get_columns(), data


def get_columns():
	return [
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("SO"), "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 130},
		{"label": _("Date"), "fieldname": "so_date", "fieldtype": "Date", "width": 100},
		{"label": _("Pending Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Reserved Qty"), "fieldname": "reserved_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Short to Complete"), "fieldname": "short_to_complete", "fieldtype": "Float", "width": 140},
		{"label": _("Reservable Qty"), "fieldname": "reservable_now", "fieldtype": "Float", "width": 130},
		{"label": _("To Reserve Qty"), "fieldname": "reserve_qty", "fieldtype": "Float", "width": 130},
		{"label": _("FG Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "hidden": 1, "width": 120},
		{"label": _("SO Item"), "fieldname": "sales_order_item", "fieldtype": "Data", "hidden": 1, "width": 100},
		{"label": _("Existing SRE"), "fieldname": "existing_sre", "fieldtype": "Data", "hidden": 1, "width": 100},
	]


def _get_so_date_map(sos, date_basis):
	"""{sales_order: date} using the Sales Order field chosen by the Date Basis
	dropdown (same resolver the report filter uses)."""
	if not sos:
		return {}
	field = _resolve_date_field(date_basis)
	rows = frappe.get_all("Sales Order", filters={"name": ["in", sos]}, fields=["name", field])
	return {r["name"]: r.get(field) for r in rows}


def get_line_reserved_map(so_item_names):
	"""{so_item: {reserved_qty, sre_names}} — active (docstatus 1) SREs per SO
	Item line in STOCK_WAREHOUSE. `voucher_detail_no` on the SRE is the SO Item
	line name."""
	so_item_names = [n for n in so_item_names if n]
	if not so_item_names or not frappe.db.table_exists("Stock Reservation Entry"):
		return {}

	rows = frappe.db.sql(
		"""
		SELECT
			sre.voucher_detail_no AS so_item,
			sre.name AS sre_name,
			sre.reserved_qty AS reserved_qty
		FROM `tabStock Reservation Entry` sre
		WHERE sre.docstatus = 1
			AND sre.voucher_type = 'Sales Order'
			AND sre.warehouse = %(warehouse)s
			AND sre.voucher_detail_no IN %(items)s
		""",
		{"warehouse": STOCK_WAREHOUSE, "items": so_item_names},
		as_dict=True,
	)
	out = {}
	for r in rows:
		entry = out.setdefault(r.so_item, frappe._dict(reserved_qty=0.0, sre_names=[]))
		entry.reserved_qty += flt(r.reserved_qty)
		entry.sre_names.append(r.sre_name)
	return out


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #

@frappe.whitelist()
def create_reservations(rows):
	"""Create SREs for the given lines, capped at each item's current free stock
	in STOCK_WAREHOUSE (FIFO by the order rows arrive, which the client sends in
	the report's FIFO order). Uses ERPNext's native Sales Order reservation so
	availability, serial/batch, and ledger rules are enforced."""
	if not frappe.has_permission("Stock Reservation Entry", "create"):
		frappe.throw(_("You are not permitted to create Stock Reservation Entries."), frappe.PermissionError)

	rows = frappe.parse_json(rows) or []
	if not rows:
		frappe.throw(_("No lines with a Reserve Qty were provided."))

	# Current free stock per item in the warehouse.
	items = sorted({r.get("item_code") for r in rows if r.get("item_code")})
	stock_map = get_stock_map(items)
	free_left = {
		it: flt((stock_map.get(it) or {}).get("actual_qty")) - flt((stock_map.get(it) or {}).get("reserved_qty"))
		for it in items
	}

	# Cap each request and group by SO for the native call.
	by_so = {}
	capped = 0
	for r in rows:
		item_code = r.get("item_code")
		qty = flt(r.get("qty"))
		if not item_code or not r.get("sales_order_item") or qty <= 0:
			continue
		allowed = max(0.0, min(qty, free_left.get(item_code, 0.0)))
		if allowed < qty:
			capped += 1
		if allowed <= 0:
			continue
		free_left[item_code] -= allowed
		by_so.setdefault(r.get("sales_order"), []).append(
			{"sales_order_item": r.get("sales_order_item"), "qty_to_reserve": allowed, "warehouse": STOCK_WAREHOUSE}
		)

	created = 0
	skipped_sos = []
	for so, items_details in by_so.items():
		try:
			so_doc = frappe.get_doc("Sales Order", so)
			# ERPNext 15 native reservation. Signature verified against the site;
			# reserves the given SO item lines in the given warehouse.
			so_doc.create_stock_reservation_entries(items_details=items_details, notify=False)
			created += len(items_details)
		except Exception:
			frappe.log_error(title="FG Stock Reservation Manager: create failed for {0}".format(so))
			skipped_sos.append(so)

	if skipped_sos:
		frappe.msgprint(
			_("Could not reserve on these Sales Orders (see Error Log): {0}").format(", ".join(skipped_sos)),
			indicator="orange",
			alert=True,
		)

	return {"created": created, "capped": capped, "skipped": len(skipped_sos)}


@frappe.whitelist()
def cancel_reservations(sre_names):
	"""Cancel (and delete) the given Stock Reservation Entries, releasing the
	reserved qty back to free stock."""
	if not frappe.has_permission("Stock Reservation Entry", "cancel"):
		frappe.throw(_("You are not permitted to cancel Stock Reservation Entries."), frappe.PermissionError)

	names = frappe.parse_json(sre_names) or []
	# Flatten comma-joined "existing_sre" values the client may pass.
	flat = []
	for n in names:
		flat.extend([x for x in str(n).split(",") if x])

	cancelled = 0
	for name in dict.fromkeys(flat):
		if not frappe.db.exists("Stock Reservation Entry", name):
			continue
		doc = frappe.get_doc("Stock Reservation Entry", name)
		if doc.docstatus == 1:
			doc.cancel()
		if frappe.db.exists("Stock Reservation Entry", name):
			frappe.delete_doc("Stock Reservation Entry", name, force=True, ignore_permissions=False)
		cancelled += 1

	return {"cancelled": cancelled}
