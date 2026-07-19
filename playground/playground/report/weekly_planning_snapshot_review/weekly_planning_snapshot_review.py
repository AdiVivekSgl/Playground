# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Weekly Planning Snapshot Review
=================================

The "Friday screen": diffs the live open-SO pull against the last APPROVED
(submitted) Weekly Planning Snapshot, and computes a live per-line status.

Reused, not reimplemented (see those modules for the actual queries):
  - get_open_so_items / get_item_map / get_stock_map   (production_requirement_report)
  - get_line_reserved_map                              (fg_stock_reservation_manager)

Item Free Stock column/field = actual_qty - reservations against SALES ORDERS
only (Stock Reservation Entry, voucher_type = Sales Order, STOCK_WAREHOUSE) for
that item - a stable per-item fact, chosen deliberately over Reservable Qty
(FGSRM's per-line, FIFO-allocated figure) because this value gets frozen into an
immutable snapshot: it means the same thing on re-read weeks later. Non-SO
reservations (production/pick-list/etc.) do not reduce it.

Suggested Prodn (same logic as the FGSRM report):
  Short to Complete = max(0, Pending - Reserved)
  Suggested Prodn   = max(0, Short to Complete - Item Free Stock + Buffer)
Buffer defaults from the item's safety stock and is editable in the grid (client
side, recomputes Suggested Prodn). Toggles: "Only Suggested Prodn > 0" and
"Consolidated Suggested Prodn" (one row per item: Item Name, Item Free Stock,
total Suggested Prodn).

Diff (by sales_order_item, the SO Item child row name):
  - In both, same pending_qty      -> Unchanged
  - In both, different pending_qty -> Qty Changed (Qty Delta shown)
  - In fresh pull only             -> New
  - In baseline only               -> Closed (its live Status explains why -
    Dispatched, Cancelled, or Removed from SO; never assumed)

Live Status (evaluated for EVERY line, whether or not it's still in the open-SO
pull - a line can go Awaiting Production -> Partially Dispatched while still
technically "open"):
  1. Cancelled             - SO docstatus = 2, or SO status in (Cancelled, Closed)
  2. Dispatched             - delivered_qty >= qty
  3. Partially Dispatched   - 0 < delivered_qty < qty
  4. Production Completed   - delivered_qty = 0; a Work Order for this line is
                              Completed, confirmed by a submitted Manufacture
                              Stock Entry against it (defensive double-check)
  5. In Production          - delivered_qty = 0; a Work Order for this line is
                              In Process
  6. Stopped / Closed       - delivered_qty = 0; the only Work Order(s) for this
                              line are Stopped/Closed - surfaced distinctly, not
                              folded into Awaiting Production
  7. Awaiting Production    - delivered_qty = 0; no Work Order yet, or only
                              Draft/Not Started ones
  Removed from SO           - the Sales Order Item itself no longer exists
                              (item removed from the SO via amendment)
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

from playground.playground.report.production_requirement_report.production_requirement_report import (
	STOCK_WAREHOUSE,
	get_open_so_items,
	get_item_map,
	get_stock_map,
)
from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
	get_line_reserved_map,
)


def execute(filters=None):
	filters = filters or {}

	so_items = get_open_so_items(filters)
	fresh = {r.so_item: r for r in so_items}
	line_reserved = get_line_reserved_map(list(fresh.keys()))

	baseline_items = _get_baseline_items()
	baseline = {b.sales_order_item: b for b in baseline_items}

	fg_items = sorted({r.item_code for r in so_items} | {b.item_code for b in baseline_items if b.item_code})
	item_map = get_item_map(fg_items)
	# Live Item Free Stock = actual - reservations made against SALES ORDERS only
	# (Stock Reservation Entry, voucher_type = Sales Order, STOCK_WAREHOUSE) - so
	# non-SO reservations (e.g. production/pick-list) don't reduce it. Fresh lines
	# use this live figure; Closed (baseline-only) lines keep their FROZEN value -
	# the point of a snapshot fact is what it was at approval time.
	stock_map = get_stock_map(fg_items)
	so_reserved_map = _so_reserved_map(fg_items)

	# Preserve fresh-pull order, then append any baseline-only (closed) keys.
	all_keys = [r.so_item for r in so_items]
	for key in baseline.keys():
		if key not in fresh:
			all_keys.append(key)

	statuses = compute_line_statuses(all_keys)

	data = []
	for key in all_keys:
		f = fresh.get(key)
		b = baseline.get(key)

		delta = None
		if f and b:
			raw_delta = flt(f.pending_qty) - flt(b.pending_qty)
			if abs(raw_delta) < 0.0001:
				bucket = _("Unchanged")
			else:
				bucket = _("Qty Changed")
				delta = raw_delta
		elif f and not b:
			bucket = _("New")
		else:
			bucket = _("Closed")

		if f:
			item_code = f.item_code
			customer = f.customer
			sales_order = f.sales_order
			so_date = f.transaction_date
			pending_qty = flt(f.pending_qty)
			reserved_qty = flt((line_reserved.get(key) or {}).get("reserved_qty"))
			item_free_stock = flt((stock_map.get(item_code) or {}).get("actual_qty")) - flt(
				so_reserved_map.get(item_code, 0.0)
			)
		else:
			item_code = b.item_code
			customer = b.customer
			sales_order = b.sales_order
			so_date = b.so_date
			pending_qty = flt(b.pending_qty)
			reserved_qty = flt(b.reserved_qty)
			item_free_stock = flt(b.get("item_free_stock"))

		item_name = (item_map.get(item_code) or {}).get("item_name") if item_code else None
		if not item_name and b:
			item_name = b.item_name

		# Suggested Prodn - same logic as the FGSRM report:
		#   Short to Complete = max(0, pending - reserved)
		#   Suggested Prodn   = max(0, Short - Item Free Stock + Buffer)
		# Buffer defaults from the item's safety stock and is editable in the grid.
		buffer = flt((item_map.get(item_code) or {}).get("safety_stock"))
		short_to_complete = max(0.0, pending_qty - reserved_qty)
		suggested_prodn = max(0.0, short_to_complete - flt(item_free_stock) + buffer)

		data.append(
			{
				"item_code": item_code,
				"item_name": item_name,
				"customer": customer,
				"sales_order": sales_order,
				"so_date": so_date,
				"pending_qty": pending_qty,
				"reserved_qty": reserved_qty,
				"item_free_stock": item_free_stock,
				"suggested_prodn": suggested_prodn,
				"qty_delta": delta,
				"diff_bucket": bucket,
				"status": statuses.get(key),
				"sales_order_item": key,
				"buffer": buffer,
			}
		)

	# Toggle: only rows with a positive Suggested Prodn.
	if cint(filters.get("only_suggested")):
		data = [d for d in data if flt(d["suggested_prodn"]) > 0]

	# Toggle: consolidate to one row per item (Item Name, Item Free Stock, total
	# Suggested Prodn across the item's rows).
	if cint(filters.get("consolidated")):
		data = _consolidate(data)

	return get_columns(), data


def get_columns():
	return [
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 150},
		{"label": _("SO"), "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 120},
		{"label": _("SO Date"), "fieldname": "so_date", "fieldtype": "Date", "width": 100},
		{"label": _("Pending Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Reserved Qty"), "fieldname": "reserved_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Item Free Stock"), "fieldname": "item_free_stock", "fieldtype": "Float", "width": 130},
		{"label": _("Suggested Prodn"), "fieldname": "suggested_prodn", "fieldtype": "Float", "width": 130},
		{"label": _("Qty Delta"), "fieldname": "qty_delta", "fieldtype": "Float", "width": 100},
		{"label": _("Diff Bucket"), "fieldname": "diff_bucket", "fieldtype": "Data", "width": 120},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 160},
		{"label": _("SO Item"), "fieldname": "sales_order_item", "fieldtype": "Data", "hidden": 1, "width": 100},
		# Editable (client-side) - defaults from item safety stock; recomputes
		# Suggested Prodn live. Kept at the extreme right.
		{"label": _("Buffer"), "fieldname": "buffer", "fieldtype": "Float", "width": 100},
	]


def _so_reserved_map(fg_items):
	"""{item_code: qty reserved against Sales Orders in STOCK_WAREHOUSE}. Only
	Stock Reservation Entries with voucher_type = 'Sales Order' - non-SO
	reservations are excluded so they don't reduce Item Free Stock."""
	if not fg_items or not frappe.db.table_exists("Stock Reservation Entry"):
		return {}
	rows = frappe.db.sql(
		"""
		SELECT item_code, SUM(reserved_qty) AS qty
		FROM `tabStock Reservation Entry`
		WHERE docstatus = 1 AND voucher_type = 'Sales Order'
			AND warehouse = %(wh)s AND item_code IN %(items)s
		GROUP BY item_code
		""",
		{"wh": STOCK_WAREHOUSE, "items": fg_items},
		as_dict=True,
	)
	return {r.item_code: flt(r.qty) for r in rows}


def _consolidate(rows):
	"""One row per item: Item Name, Item Free Stock (per-item), and the total
	Suggested Prodn across the item's rows. All other columns are left blank."""
	out = {}
	order = []
	for r in rows:
		ic = r["item_code"]
		if ic not in out:
			out[ic] = {
				"item_code": ic,
				"item_name": r["item_name"],
				"item_free_stock": r["item_free_stock"],
				"suggested_prodn": 0.0,
			}
			order.append(ic)
		out[ic]["suggested_prodn"] += flt(r["suggested_prodn"])
	return [out[ic] for ic in order]


def _get_baseline_items():
	"""Items of the most recently SUBMITTED Weekly Planning Snapshot, or []
	if none exists yet (first-ever run - everything shows as New)."""
	latest = frappe.get_all(
		"Weekly Planning Snapshot",
		filters={"docstatus": 1},
		fields=["name"],
		order_by="snapshot_date desc, creation desc",
		limit=1,
	)
	if not latest:
		return []
	return frappe.get_all(
		"Weekly Planning Snapshot Item",
		filters={"parent": latest[0].name},
		fields=[
			"sales_order_item",
			"sales_order",
			"item_code",
			"item_name",
			"customer",
			"so_date",
			"pending_qty",
			"reserved_qty",
			"item_free_stock",
		],
	)


def compute_line_statuses(so_item_names):
	"""{sales_order_item: status_label} for every given SO Item line name,
	whether or not it's still part of the current open-SO pull. See module
	docstring for the full status table and evaluation order."""
	so_item_names = list({n for n in so_item_names if n})
	if not so_item_names:
		return {}

	so_item_rows = frappe.get_all(
		"Sales Order Item",
		filters={"name": ["in", so_item_names]},
		fields=["name", "parent", "qty", "delivered_qty"],
	)
	found = {r.name: r for r in so_item_rows}

	parents = sorted({r.parent for r in so_item_rows})
	so_rows = (
		frappe.get_all(
			"Sales Order", filters={"name": ["in", parents]}, fields=["name", "docstatus", "status"]
		)
		if parents
		else []
	)
	so_map = {r.name: r for r in so_rows}

	wo_rows = frappe.get_all(
		"Work Order",
		filters={"sales_order_item": ["in", so_item_names]},
		fields=["sales_order_item", "status", "name"],
	)
	wo_map = {}
	for w in wo_rows:
		wo_map.setdefault(w.sales_order_item, []).append(w)

	# Defensive confirmation for "Production Completed": a Work Order can't
	# reach Completed without a submitted Manufacture Stock Entry in normal
	# operation, but a manually-forced status change shouldn't be trusted blind.
	completed_wo_names = [w.name for rows in wo_map.values() for w in rows if w.status == "Completed"]
	se_confirmed = set()
	if completed_wo_names:
		se_rows = frappe.get_all(
			"Stock Entry",
			filters={"work_order": ["in", completed_wo_names], "purpose": "Manufacture", "docstatus": 1},
			fields=["work_order"],
		)
		se_confirmed = {r.work_order for r in se_rows}

	statuses = {}
	for name in so_item_names:
		soi = found.get(name)
		if not soi:
			statuses[name] = _("Removed from SO")
			continue

		so = so_map.get(soi.parent)
		if so and (so.docstatus == 2 or so.status in ("Cancelled", "Closed")):
			statuses[name] = _("Cancelled")
			continue

		qty = flt(soi.qty)
		delivered = flt(soi.delivered_qty)

		if qty > 0 and delivered >= qty:
			statuses[name] = _("Dispatched")
			continue
		if delivered > 0:
			statuses[name] = _("Partially Dispatched")
			continue

		# delivered_qty == 0 from here on.
		work_orders = wo_map.get(name) or []
		completed = [w for w in work_orders if w.status == "Completed" and w.name in se_confirmed]
		in_process = [w for w in work_orders if w.status == "In Process"]
		stopped = [w for w in work_orders if w.status in ("Stopped", "Closed")]

		if completed:
			statuses[name] = _("Production Completed")
		elif in_process:
			statuses[name] = _("In Production")
		elif stopped:
			statuses[name] = _("Stopped / Closed")
		else:
			statuses[name] = _("Awaiting Production")

	return statuses


@frappe.whitelist()
def approve_snapshot(filters=None):
	"""Freezes the current open-SO demand into a new Weekly Planning Snapshot,
	inserted AND submitted in one action (same person generates and approves -
	no maker-checker split). Recomputes from `filters` server-side rather than
	trusting client-sent row data, matching create_production_plan's
	convention elsewhere in this app."""
	filters = frappe.parse_json(filters) if filters else {}

	if not frappe.has_permission("Weekly Planning Snapshot", "create"):
		frappe.throw(_("Not permitted to create a Weekly Planning Snapshot."), frappe.PermissionError)

	so_items = get_open_so_items(filters)
	if not so_items:
		frappe.throw(_("No open Sales Order lines to snapshot."))

	reserved_map = get_line_reserved_map([r.so_item for r in so_items])
	fg_items = sorted({r.item_code for r in so_items})
	item_map = get_item_map(fg_items)
	# Item Free Stock frozen at approval time - actual minus reservations made
	# against SALES ORDERS only (STOCK_WAREHOUSE), matching the report's live
	# basis. A stable per-item fact, not the FIFO-allocated Reservable Qty.
	stock_map = get_stock_map(fg_items)
	so_reserved_map = _so_reserved_map(fg_items)

	snap = frappe.new_doc("Weekly Planning Snapshot")
	snap.snapshot_date = frappe.utils.nowdate()
	for r in so_items:
		res = reserved_map.get(r.so_item) or frappe._dict()
		stock = stock_map.get(r.item_code) or frappe._dict()
		snap.append(
			"items",
			{
				"sales_order": r.sales_order,
				"sales_order_item": r.so_item,
				"item_code": r.item_code,
				"item_name": (item_map.get(r.item_code) or {}).get("item_name"),
				"customer": r.customer,
				"so_date": r.transaction_date,
				"pending_qty": r.pending_qty,
				"reserved_qty": flt(res.get("reserved_qty")),
				"item_free_stock": flt(stock.get("actual_qty")) - flt(so_reserved_map.get(r.item_code, 0.0)),
			},
		)

	snap.insert()
	snap.submit()
	return snap.name
