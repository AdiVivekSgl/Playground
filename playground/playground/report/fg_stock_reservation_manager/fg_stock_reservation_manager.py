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
    reservation path. The cap follows the "Unreserved Stock Basis" filter, same
    as the Item Free Stock column - so what's enforced always matches what was
    shown. Under "Only Displayed SOs", that cap deliberately ignores
    reservations tied to other SOs, so ERPNext's own reservation call can still
    reject a request; when it does, the response's `blocked` map lists the
    OTHER active reservations against that item so the user can cancel one and
    retry, rather than a bare failure.
  - Cancel Reservations: cancel the SREs on the selected lines, releasing stock.
  - Dispatch Priority Date (the "Date" column) is editable only when Date
    Basis = "Custom Updated Delivery Date" - a header-level SO field, so
    editing it on any row applies to every row sharing that SO. Persisted via
    update_dispatch_priority_date, guarded by Sales Order write permission.
  - Ready to Dispatch / Possible to Complete: view filters that narrow the
    report to qualifying Sales Orders -
      Ready to Dispatch    -> Short to Complete == 0 on every line (nothing
                              left to reserve)
      Possible to Complete -> Short to Complete <= Item Free Stock on every
                              line (every shortfall is coverable by a
                              reservation), excluding SOs that already qualify
                              for Ready to Dispatch
    Both ignore "Only lines with unreserved pending" while active, since they
    need to see every line of a SO to judge whether all of them qualify.

Prerequisite: "Stock Reservation" must be enabled in Stock Settings.

Heavy reuse of the Production Requirement Report helpers so the two screens stay
consistent.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate
from datetime import date

from playground.playground.report.production_requirement_report.production_requirement_report import (
	STOCK_WAREHOUSE,
	CUSTOM_DELIVERY_DATE_FIELD,
	get_open_so_items,
	get_stock_map,
	get_reserved_in_stock_warehouse_map,
	get_item_map,
	compute_so_qualification_flags,
	_get_so_header_map,
	_resolve_date_field,
)


def execute(filters=None):
	filters = filters or {}
	view_mode = filters.get("view_mode") or ""

	so_items = get_open_so_items(filters)
	if not so_items:
		return get_columns(), []

	fg_items = sorted(set(r.item_code for r in so_items))
	sos = sorted(set(r.sales_order for r in so_items))
	line_reserved = get_line_reserved_map([r.so_item for r in so_items])
	stock_map = get_stock_map(fg_items)
	item_map = get_item_map(fg_items)

	# Item-level reservation totals + per-customer breakdown, honouring the
	# Unreserved Stock Basis toggle (All Reservations vs Only Displayed SOs).
	item_reservations = get_item_reservation_breakdown(
		fg_items, filters.get("unreserved_basis") or "All Reservations", sos
	)

	# "Date" column shows the SO date chosen by the Date Basis dropdown.
	so_date_map = _get_so_date_map(sos, filters.get("date_basis"))

	# Item free stock is shared across an item's SO lines; deduct as we walk the
	# lines (FIFO by delivery date) so "Reservable Qty" doesn't over-promise the
	# same units to two lines.
	so_header = _get_so_header_map(sos)

	def _sort_key(r):
		sd = so_header.get(r.sales_order, {}).get("sort_date")
		return (getdate(sd) if sd else date.max, r.sales_order, r.item_code)

	# Free stock basis (mirrors the PRR's "Unreserved Stock Basis" filter):
	#   All Reservations   -> actual − Bin.reserved_qty (every reservation)
	#   Only Displayed SOs -> actual − reservations tied to the shown SOs
	unreserved_basis = filters.get("unreserved_basis") or "All Reservations"
	displayed_reserved = (
		get_reserved_in_stock_warehouse_map(sos)
		if unreserved_basis == "Only Displayed SOs"
		else {}
	)
	# Stable per-item free stock, for the "Item Free Stock" column and the
	# "Possible to Complete" view — NOT decremented as lines are processed.
	# `free_left` below is a separate working copy consumed FIFO purely to
	# compute each line's Reservable Qty (so two lines sharing an item can't
	# both claim the same units).
	item_free_stock_map = {}
	for item in fg_items:
		stock = stock_map.get(item) or frappe._dict()
		if unreserved_basis == "Only Displayed SOs":
			reserved_from_stock = displayed_reserved.get(item, 0.0) or 0.0
		else:
			reserved_from_stock = flt(stock.get("reserved_qty"))
		item_free_stock_map[item] = flt(stock.get("actual_qty")) - reserved_from_stock
	free_left = dict(item_free_stock_map)

	only_unreserved = cint(filters.get("only_unreserved"))
	# A view button wants to see the SO's whole picture (to judge whether ALL
	# of its lines qualify), not a pre-filtered subset — ignore "only lines
	# with unreserved pending" while a view is active.
	if view_mode:
		only_unreserved = 0

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

		breakdown = item_reservations.get(r.item_code) or {"total": 0.0, "by_customer": {}}

		# Suggested Prodn = shortfall not coverable by this item's free stock =
		# max(0, Short to Complete − Item Free Stock) — mirrors the PRR's
		# "Required to Produce" netting, computed from the two adjacent columns.
		this_item_free = flt(item_free_stock_map.get(r.item_code, 0.0))
		suggested_prodn = max(0.0, short_to_complete - this_item_free)

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
				"item_free_stock": item_free_stock_map.get(r.item_code, 0.0),
				"suggested_prodn": suggested_prodn,
				"total_reserved_qty": flt(breakdown["total"]),
				"reserved_by_customer": _format_customer_breakdown(breakdown["by_customer"]),
				"reservable_now": reservable,
				"reserve_qty": reservable,
				"existing_sre": ",".join(res.get("sre_names") or []),
			}
		)

	# Rows are already ordered so that a Sales Order's lines are adjacent (see
	# _sort_key above: date, then sales_order, then item_code). Mark the first
	# row of each contiguous same-SO run so the "Group by Sales Order" toggle
	# can blank the repeated SO/Customer/Date text client-side, without
	# touching the underlying values the row actions (create/cancel/select)
	# rely on. Any later view_mode filtering below only ever drops a SO's rows
	# as a whole (never partially), so this marking stays valid afterwards.
	last_so = None
	for row in data:
		row["so_group_first"] = row["sales_order"] != last_so
		last_so = row["sales_order"]

	if not view_mode:
		return get_columns(), data

	# Evaluate per-SO qualification across ALL of that SO's lines (only_unreserved
	# was forced off above, so `data` already holds every line for each SO here).
	#   ready_to_dispatch    -> Short to Complete == 0 on every line (nothing left
	#                           to reserve; already good to go)
	#   possible_to_complete -> Short to Complete <= Item Free Stock on every line
	#                           (every shortfall is coverable by a reservation),
	#                           AND at least one line still has a shortfall — SOs
	#                           already fully covered belong to Ready to Dispatch,
	#                           not here.
	# Shared with Sales Order.custom_material_status so the two never drift. In a
	# view, only_unreserved is forced off above, so `data` holds every line of
	# each SO here - iterating so_items gives the same per-SO rollup, using the
	# same item_free_stock_map (already on the report's Unreserved Stock Basis).
	so_ok = compute_so_qualification_flags(so_items, line_reserved, item_free_stock_map)

	if view_mode == "ready_to_dispatch":
		qualifying_sos = {so for so, flags in so_ok.items() if flags["ready"]}
	else:
		qualifying_sos = {
			so for so, flags in so_ok.items() if flags["coverable"] and not flags["ready"]
		}
	return get_columns(), [row for row in data if row["sales_order"] in qualifying_sos]


def get_columns():
	return [
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 200},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("SO"), "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 130},
		# Editable only when Date Basis = "Custom Updated Delivery Date" (see the
		# client script's get_datatable_options and update_dispatch_priority_date
		# below) - editing Document Creation Date or Delivery Date wouldn't make
		# sense, those are factual record-keeping dates, not a priority lever.
		{"label": _("Dispatch Priority Date"), "fieldname": "so_date", "fieldtype": "Date", "width": 150},
		{"label": _("Pending Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Reserved Qty"), "fieldname": "reserved_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Short to Complete"), "fieldname": "short_to_complete", "fieldtype": "Float", "width": 140},
		{"label": _("Item Free Stock"), "fieldname": "item_free_stock", "fieldtype": "Float", "width": 130},
		# Suggested Prodn = the shortfall that free stock can't cover, i.e. what
		# still needs to be manufactured = max(0, Short to Complete − Item Free Stock).
		{"label": _("Suggested Prodn"), "fieldname": "suggested_prodn", "fieldtype": "Float", "width": 130},
		# Item-level totals (repeated on every line of the same item), honouring
		# the Unreserved Stock Basis toggle - total reserved against Sales Orders
		# and the same figure broken down per customer.
		{"label": _("Total Reserved Qty"), "fieldname": "total_reserved_qty", "fieldtype": "Float", "width": 140},
		{"label": _("Reserved by Customer"), "fieldname": "reserved_by_customer", "fieldtype": "Data", "width": 280},
		# Kept in the row data (used to cap the editable To Reserve Qty client-side)
		# but hidden from view per request.
		{"label": _("Reservable Qty"), "fieldname": "reservable_now", "fieldtype": "Float", "width": 130, "hidden": 1},
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


def get_item_reservation_breakdown(items, unreserved_basis, displayed_sos):
	"""Per FG item: the total qty reserved against Sales Orders in
	STOCK_WAREHOUSE, plus a per-customer breakdown, honouring the Unreserved
	Stock Basis toggle:
	  All Reservations   -> every active SO reservation for the item
	  Only Displayed SOs -> only reservations tied to the SOs currently shown
	Returns {item_code: {"total": qty, "by_customer": {customer_name: qty}}}."""
	if not items or not frappe.db.table_exists("Stock Reservation Entry"):
		return {}

	params = {"items": items, "warehouse": STOCK_WAREHOUSE}
	so_condition = ""
	if unreserved_basis == "Only Displayed SOs":
		if not displayed_sos:
			return {}
		so_condition = "AND sre.voucher_no IN %(sos)s"
		params["sos"] = displayed_sos

	rows = frappe.db.sql(
		"""
		SELECT
			sre.item_code AS item_code,
			COALESCE(NULLIF(cust.customer_name, ''), so.customer) AS customer_name,
			SUM(sre.reserved_qty) AS reserved_qty
		FROM `tabStock Reservation Entry` sre
		INNER JOIN `tabSales Order` so ON so.name = sre.voucher_no
		LEFT JOIN `tabCustomer` cust ON cust.name = so.customer
		WHERE sre.docstatus = 1
			AND sre.voucher_type = 'Sales Order'
			AND sre.warehouse = %(warehouse)s
			AND sre.item_code IN %(items)s
			{so_condition}
		GROUP BY sre.item_code, customer_name
		""".format(so_condition=so_condition),
		params,
		as_dict=True,
	)

	out = {}
	for r in rows:
		entry = out.setdefault(r.item_code, {"total": 0.0, "by_customer": {}})
		qty = flt(r.reserved_qty)
		if qty <= 0:
			continue
		entry["total"] += qty
		name = r.customer_name or _("Unknown")
		entry["by_customer"][name] = entry["by_customer"].get(name, 0.0) + qty
	return out


def _format_customer_breakdown(by_customer):
	"""'Jagmal(3), SS Ent(19), Adhya(5)' - customers with a reservation for the
	item, biggest first, qty in brackets (trailing '.0' trimmed)."""
	if not by_customer:
		return ""
	pairs = sorted(by_customer.items(), key=lambda kv: kv[1], reverse=True)
	return ", ".join("{0}({1})".format(name, "%g" % flt(qty)) for name, qty in pairs)


# --------------------------------------------------------------------------- #
# Actions
# --------------------------------------------------------------------------- #

@frappe.whitelist()
def update_dispatch_priority_date(sales_order, new_date):
	"""Persist an inline Dispatch Priority Date edit back to
	Sales Order.custom_updated_delivery_date. A date is a header-level SO
	field (one per SO, not per line) - the client applies the same edit to
	every row sharing that SO before calling this, so only one write happens
	per edit regardless of how many lines that SO has. Guarded by Sales Order
	write permission and the same has_column defensive check
	_resolve_date_field uses elsewhere (the field is a custom field that may
	not exist on every site)."""
	if not frappe.db.has_column("Sales Order", CUSTOM_DELIVERY_DATE_FIELD):
		frappe.throw(
			_("The {0} field does not exist on Sales Order on this site.").format(
				CUSTOM_DELIVERY_DATE_FIELD
			)
		)
	if not frappe.has_permission("Sales Order", "write", doc=sales_order):
		frappe.throw(
			_("You are not permitted to edit Sales Order {0}.").format(sales_order),
			frappe.PermissionError,
		)
	frappe.db.set_value("Sales Order", sales_order, CUSTOM_DELIVERY_DATE_FIELD, new_date)
	return new_date


@frappe.whitelist()
def create_reservations(rows, filters=None):
	"""Create SREs for the given lines, capped at each item's current free stock
	in STOCK_WAREHOUSE (FIFO by the order rows arrive, which the client sends in
	the report's FIFO order). Uses ERPNext's native Sales Order reservation so
	availability, serial/batch, and ledger rules are enforced.

	The free-stock cap follows the report's own "Unreserved Stock Basis"
	filter (`filters.unreserved_basis`), computed the SAME way execute() does -
	so what's capped here matches what was shown on screen. Under "Only
	Displayed SOs", this deliberately ignores reservations tied to other SOs,
	which means ERPNext's own native call can still reject a request our cap
	allowed (the other reservations are real, physical commitments - we just
	chose to look past them for planning purposes). When that happens, rather
	than only logging and skipping, this returns a `blocked` map of
	{item_code: [{name, voucher_no, reserved_qty}, ...]} - the OTHER active
	reservations against that item - so the client can offer to cancel one
	and retry, instead of a bare failure."""
	if not frappe.has_permission("Stock Reservation Entry", "create"):
		frappe.throw(_("You are not permitted to create Stock Reservation Entries."), frappe.PermissionError)

	rows = frappe.parse_json(rows) or []
	if not rows:
		frappe.throw(_("No lines with a Reserve Qty were provided."))

	filters = frappe.parse_json(filters) if filters else {}
	unreserved_basis = filters.get("unreserved_basis") or "All Reservations"

	items = sorted({r.get("item_code") for r in rows if r.get("item_code")})
	stock_map = get_stock_map(items)

	if unreserved_basis == "Only Displayed SOs":
		# Match execute()'s definition exactly: net out only reservations tied
		# to the Sales Orders currently in view under these same filters.
		so_items = get_open_so_items(filters)
		displayed_sos = sorted({r.sales_order for r in so_items})
		displayed_reserved = get_reserved_in_stock_warehouse_map(displayed_sos)
		free_left = {
			it: flt((stock_map.get(it) or {}).get("actual_qty")) - flt(displayed_reserved.get(it, 0.0))
			for it in items
		}
	else:
		free_left = {
			it: flt((stock_map.get(it) or {}).get("actual_qty")) - flt((stock_map.get(it) or {}).get("reserved_qty"))
			for it in items
		}

	# Cap each request and group by SO for the native call.
	so_item_to_item = {}
	by_so = {}
	capped = 0
	for r in rows:
		item_code = r.get("item_code")
		qty = flt(r.get("qty"))
		if not item_code or not r.get("sales_order_item") or qty <= 0:
			continue
		so_item_to_item[r.get("sales_order_item")] = item_code
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
	blocked = {}
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
			for detail in items_details:
				item_code = so_item_to_item.get(detail["sales_order_item"])
				if not item_code or item_code in blocked:
					continue
				others = _get_other_reservations(item_code, exclude_so=so)
				if others:
					blocked[item_code] = others

	if skipped_sos:
		frappe.msgprint(
			_("Could not reserve on these Sales Orders (see Error Log): {0}").format(", ".join(skipped_sos)),
			indicator="orange",
			alert=True,
		)

	return {"created": created, "capped": capped, "skipped": len(skipped_sos), "blocked": blocked}


def _get_other_reservations(item_code, exclude_so):
	"""Active (docstatus 1) SREs for this item in STOCK_WAREHOUSE against any
	Sales Order OTHER than `exclude_so` - candidates the user could cancel to
	free up enough stock for a reservation ERPNext's native call just
	rejected. Returns [{name, voucher_no, reserved_qty}, ...]."""
	return frappe.get_all(
		"Stock Reservation Entry",
		filters={
			"docstatus": 1,
			"item_code": item_code,
			"warehouse": STOCK_WAREHOUSE,
			"voucher_type": "Sales Order",
			"voucher_no": ["!=", exclude_so],
		},
		fields=["name", "voucher_no", "reserved_qty"],
		order_by="reserved_qty desc",
	)


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


# --------------------------------------------------------------------------- #
# Create Production Plan (from Suggested Prodn)
# --------------------------------------------------------------------------- #

def _suggested_prodn_by_item(filters):
	"""Itemwise Suggested Prodn for the current filters = max(0, Σ Short to
	Complete − Item Free Stock), computed once per item.

	NB: this aggregates at the ITEM level rather than naively summing the
	per-line "Suggested Prodn" column - free stock is shared across an item's SO
	lines, so summing the per-line figure (which subtracts the full free stock on
	every line) would misstate the true net requirement. Same demand/free-stock
	inputs and Unreserved Stock Basis as the report, so the totals reconcile with
	what's on screen. Only the item/customer/date filters apply here (the
	only_unreserved / view_mode display toggles don't change true open demand).

	Returns {item_code: qty} for items with a positive requirement."""
	so_items = get_open_so_items(filters)
	if not so_items:
		return {}

	fg_items = sorted(set(r.item_code for r in so_items))
	sos = sorted(set(r.sales_order for r in so_items))
	line_reserved = get_line_reserved_map([r.so_item for r in so_items])
	stock_map = get_stock_map(fg_items)

	unreserved_basis = filters.get("unreserved_basis") or "All Reservations"
	displayed_reserved = (
		get_reserved_in_stock_warehouse_map(sos)
		if unreserved_basis == "Only Displayed SOs"
		else {}
	)
	item_free = {}
	for item in fg_items:
		stock = stock_map.get(item) or frappe._dict()
		if unreserved_basis == "Only Displayed SOs":
			reserved_from_stock = displayed_reserved.get(item, 0.0) or 0.0
		else:
			reserved_from_stock = flt(stock.get("reserved_qty"))
		item_free[item] = flt(stock.get("actual_qty")) - reserved_from_stock

	short_by_item = {}
	for r in so_items:
		res = line_reserved.get(r.so_item) or frappe._dict()
		short = max(0.0, flt(r.pending_qty) - flt(res.get("reserved_qty")))
		short_by_item[r.item_code] = short_by_item.get(r.item_code, 0.0) + short

	out = {}
	for item, short in short_by_item.items():
		qty = max(0.0, short - flt(item_free.get(item, 0.0)))
		if qty > 0:
			out[item] = qty
	return out


@frappe.whitelist()
def create_production_plan_from_suggested_prodn(filters=None):
	"""Create a DRAFT Production Plan from the report's itemwise Suggested Prodn,
	then populate the full nested sub-assembly chain and the raw materials for
	purchase, and save it (never auto-submitted). Recomputed server-side from
	`filters` so it matches the report.

	Steps mirror the manual Production Plan workflow:
	  1. One po_items row per item = its Suggested Prodn (needs an active default
	     BOM; items without one are skipped and reported).
	  2. "Display Zero Value" -> ignore_existing_ordered_qty, so the raw-material
	     planning surfaces every required line rather than netting some to zero.
	  3. Get Sub Assembly Items -> "Full Nested Chain" (BOM exploded through all
	     sub-assembly levels).
	  4. Get Raw Materials for Purchase -> Material Request Plan Items.

	Steps 3 & 4 call ERPNext's own Production Plan routines so behaviour matches
	the buttons on the form; each is wrapped defensively so a signature/version
	difference logs and degrades rather than aborting the whole action. The exact
	field/method mapping for "Display Zero Value" and "Full Nested Chain" is
	ERPNext-version dependent - verify against the form on your site."""
	if not frappe.has_permission("Production Plan", "create"):
		frappe.throw(_("You are not permitted to create Production Plans."), frappe.PermissionError)

	filters = frappe.parse_json(filters) if filters else {}

	prodn_by_item = _suggested_prodn_by_item(filters)
	if not prodn_by_item:
		frappe.throw(_("Nothing to produce — every item's Suggested Prodn is zero for this view."))

	company = (
		frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)
	if not company:
		frappe.throw(_("No default Company found. Please set one before creating a Production Plan."))

	pp = frappe.new_doc("Production Plan")
	pp.company = company
	# "Display Zero Value": show every required raw material, including lines an
	# existing projected/ordered qty would otherwise net to zero.
	if pp.meta.has_field("ignore_existing_ordered_qty"):
		pp.ignore_existing_ordered_qty = 1

	skipped = []
	for item, qty in prodn_by_item.items():
		item_detail = frappe.db.get_value("Item", item, ["stock_uom", "default_bom"], as_dict=True) or frappe._dict()
		bom_no = item_detail.default_bom or frappe.db.get_value(
			"BOM", {"item": item, "is_default": 1, "is_active": 1, "docstatus": 1}, "name"
		)
		if not bom_no:
			skipped.append(item)
			continue
		pp.append(
			"po_items",
			{
				"item_code": item,
				"bom_no": bom_no,
				"planned_qty": qty,
				"planned_start_date": nowdate(),
				"stock_uom": item_detail.stock_uom,
				"warehouse": STOCK_WAREHOUSE,
			},
		)

	if not pp.get("po_items"):
		frappe.throw(
			_("No Production Plan rows — none of the items with Suggested Prodn have an active default BOM.")
		)

	pp.insert()

	# 3. Full Nested Chain: explode every po_item's BOM through all sub-assembly
	# levels, then persist. Guarded + saved on its own so a failure here (or in
	# step 4) still leaves a usable draft rather than aborting the whole action.
	sub_assembly_count = 0
	try:
		pp.get_sub_assembly_items()
		pp.save()
		sub_assembly_count = len(pp.get("sub_assembly_items") or [])
	except Exception:
		frappe.log_error(title="FGSRM Create Prodn Plan: get_sub_assembly_items failed for {0}".format(pp.name))
		pp.reload()

	# 4. Get Raw Materials for Purchase -> Material Request Plan Items.
	raw_material_count = 0
	try:
		from erpnext.manufacturing.doctype.production_plan.production_plan import (
			get_items_for_material_requests,
		)

		mr_items = get_items_for_material_requests(pp.as_dict()) or []
		for d in mr_items:
			pp.append("mr_items", d)
		pp.save()
		raw_material_count = len(pp.get("mr_items") or [])
	except Exception:
		frappe.log_error(title="FGSRM Create Prodn Plan: get_items_for_material_requests failed for {0}".format(pp.name))
		pp.reload()

	if skipped:
		frappe.msgprint(
			_("Skipped (no active default BOM): {0}").format(", ".join(sorted(set(skipped)))),
			indicator="orange",
			alert=True,
		)

	return {
		"name": pp.name,
		"items": len(pp.get("po_items") or []),
		"sub_assemblies": sub_assembly_count,
		"raw_materials": raw_material_count,
		"skipped": skipped,
	}
