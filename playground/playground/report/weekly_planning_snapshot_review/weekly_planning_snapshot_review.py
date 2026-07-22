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

Item Free Stock column/field = actual_qty - Bin.reserved_qty (ALL reservations,
STOCK_WAREHOUSE) for that item - a stable per-item fact, chosen deliberately
over Reservable Qty (FGSRM's per-line, FIFO-allocated figure) because this
value gets frozen into an immutable snapshot: it means the same thing on
re-read weeks later, regardless of which other lines happened to be in view
when it was computed.

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
	CUSTOM_DELIVERY_DATE_FIELD,
	get_open_so_items,
	get_item_map,
	get_stock_map,
)
from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
	get_line_reserved_map,
)
from playground.playground.fgsrm_manual_requirement import list_manual_requirements

# Synthetic sales_order_item prefix for a Projected (manual) snapshot line - it
# has no real Sales Order Item behind it. Kept unique + traceable so the Snapshot
# Review diff (keyed by sales_order_item) neither collapses several projected
# rows together nor mistakes one for a Sales Order line.
MANUAL_SO_ITEM_PREFIX = "MANUAL-"


def execute(filters=None):
	filters = filters or {}

	so_items = get_open_so_items(filters)
	fresh = {r.so_item: r for r in so_items}
	line_reserved = get_line_reserved_map(list(fresh.keys()))

	baseline_items = _get_baseline_items()
	baseline = {b.sales_order_item: b for b in baseline_items}

	fg_items = sorted({r.item_code for r in so_items} | {b.item_code for b in baseline_items if b.item_code})
	item_map = get_item_map(fg_items)
	# Live Item Free Stock (actual - ALL reservations, STOCK_WAREHOUSE) for
	# fresh lines. Closed (baseline-only) lines show their FROZEN value instead
	# - the point of a snapshot fact is what it was at approval time, not what
	# it is now.
	stock_map = get_stock_map(fg_items)

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
		elif str(key).startswith(MANUAL_SO_ITEM_PREFIX):
			# A Projected line from a prior snapshot: it is never in the fresh
			# open-SO pull, so don't diff it as "Closed".
			bucket = _("Projected")
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
				(stock_map.get(item_code) or {}).get("reserved_qty")
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
				"qty_delta": delta,
				"diff_bucket": bucket,
				"status": statuses.get(key),
				"sales_order_item": key,
			}
		)

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
		{"label": _("Qty Delta"), "fieldname": "qty_delta", "fieldtype": "Float", "width": 100},
		{"label": _("Diff Bucket"), "fieldname": "diff_bucket", "fieldtype": "Data", "width": 120},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 160},
		{"label": _("SO Item"), "fieldname": "sales_order_item", "fieldtype": "Data", "hidden": 1, "width": 100},
	]


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

	# Projected (manual) lines carry a synthetic MANUAL-<name> key and have no
	# Sales Order Item - label them Projected and keep them out of the SO lookups
	# below (which would otherwise report them as "Removed from SO").
	statuses = {n: _("Projected") for n in so_item_names if str(n).startswith(MANUAL_SO_ITEM_PREFIX)}
	so_item_names = [n for n in so_item_names if not str(n).startswith(MANUAL_SO_ITEM_PREFIX)]
	if not so_item_names:
		return statuses

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

	# statuses already seeded with the Projected lines above; fill the SO lines.
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
def approve_snapshot(filters=None, include_manual=0):
	"""Freeze the current open-SO demand into a new Weekly Planning Snapshot as a
	DRAFT (not submitted). The caller routes to the form so it can be reviewed -
	Committed Prodn adjusted line-wise - and then SUBMITTED (= approved). Item
	Free Stock is frozen on the SALES-ORDER-only reservation basis; Suggested
	Prodn is computed (FGSRM logic) and Committed Prodn prepopulated to match.
	Recomputes from `filters` server-side rather than trusting client-sent rows.

	When `include_manual` is set, the caller's own FGSRM manual requirements
	(under the same filters) are folded in as extra "Projected" lines - see
	_append_projected_rows. Off by default, so the snapshot stays pure open-SO
	demand unless the planner opts in."""
	filters = frappe.parse_json(filters) if filters else {}
	include_manual = cint(include_manual)

	if not frappe.has_permission("Weekly Planning Snapshot", "create"):
		frappe.throw(_("Not permitted to create a Weekly Planning Snapshot."), frappe.PermissionError)

	so_items = get_open_so_items(filters)
	manual_reqs = list_manual_requirements(filters) if include_manual else []
	if not so_items and not manual_reqs:
		frappe.throw(_("Nothing to snapshot for this view."))

	reserved_map = get_line_reserved_map([r.so_item for r in so_items])
	fg_items = sorted({r.item_code for r in so_items})
	# Stock / reservation / name maps span both SO items and any manual-only item,
	# so a Projected line can be valued and named just like a Sales Order line.
	all_items = sorted(set(fg_items) | {r["item_code"] for r in manual_reqs})
	item_map = get_item_map(all_items)
	stock_map = get_stock_map(all_items)
	so_reserved_map = _so_reserved_map(all_items)
	dispatch_date_map = _dispatch_date_map({r.sales_order for r in so_items})

	snap = frappe.new_doc("Weekly Planning Snapshot")
	snap.snapshot_date = frappe.utils.nowdate()
	for r in so_items:
		res = reserved_map.get(r.so_item) or frappe._dict()
		stock = stock_map.get(r.item_code) or frappe._dict()
		pending = flt(r.pending_qty)
		reserved = flt(res.get("reserved_qty"))
		free = flt(stock.get("actual_qty")) - flt(so_reserved_map.get(r.item_code, 0.0))
		# Suggested Prodn - FGSRM logic (no buffer). Committed Prodn starts equal
		# to it; production edits it on the draft before submission.
		suggested = max(0.0, max(0.0, pending - reserved) - free)
		snap.append(
			"items",
			{
				"sales_order": r.sales_order,
				"sales_order_item": r.so_item,
				"item_code": r.item_code,
				"item_name": (item_map.get(r.item_code) or {}).get("item_name"),
				"customer": r.customer,
				"so_date": dispatch_date_map.get(r.sales_order) or r.transaction_date,
				"pending_qty": pending,
				"reserved_qty": reserved,
				"item_free_stock": free,
				"suggested_prodn": suggested,
				"committed_prodn": suggested,
				"valuation_rate": flt(stock.get("valuation_rate")),
			},
		)

	_append_projected_rows(snap, manual_reqs, item_map, stock_map, so_reserved_map)

	snap.insert()
	return snap.name


def _append_projected_rows(snap, manual_reqs, item_map, stock_map, so_reserved_map):
	"""Fold the caller's FGSRM manual requirements into the snapshot as Projected
	lines. Each becomes an ordinary snapshot line, but:
	  - customer reads "<Customer> - Projected" (or just "Projected" when the
	    requirement carries no customer), so speculative demand is unmistakable;
	  - reserved_qty is 0 (nothing is reserved against a projection) and it nets
	    against the item's frozen free stock exactly like a Sales Order line, so
	    Suggested Prodn is comparable;
	  - sales_order is blank and sales_order_item is a synthetic MANUAL-<name>
	    key (see MANUAL_SO_ITEM_PREFIX).
	The doctype's validate() recomputes Suggested from these same inputs, so the
	values set here and the stored ones agree."""
	for req in manual_reqs:
		item = req["item_code"]
		stock = stock_map.get(item) or frappe._dict()
		qty = flt(req["qty"])
		free = flt(stock.get("actual_qty")) - flt(so_reserved_map.get(item, 0.0))
		suggested = max(0.0, qty - free)
		customer = req.get("customer")
		projected = _("{0} - Projected").format(customer) if customer else _("Projected")
		snap.append(
			"items",
			{
				"sales_order": None,
				"sales_order_item": "{0}{1}".format(MANUAL_SO_ITEM_PREFIX, req["name"]),
				"item_code": item,
				"item_name": req.get("item_name") or (item_map.get(item) or {}).get("item_name"),
				"customer": projected,
				"so_date": None,
				"pending_qty": qty,
				"reserved_qty": 0.0,
				"item_free_stock": free,
				"suggested_prodn": suggested,
				"committed_prodn": suggested,
				"valuation_rate": flt(stock.get("valuation_rate")),
			},
		)


def _dispatch_date_map(sales_orders):
	"""{sales_order: dispatch priority date} - custom_updated_delivery_date (the
	FGSRM Dispatch Priority Date), falling back to delivery_date then
	transaction_date. Guarded so it runs on a site without the custom field."""
	sales_orders = [s for s in sales_orders if s]
	if not sales_orders:
		return {}
	has_custom = frappe.db.has_column("Sales Order", CUSTOM_DELIVERY_DATE_FIELD)
	fields = ["name", "delivery_date", "transaction_date"]
	if has_custom:
		fields.append(CUSTOM_DELIVERY_DATE_FIELD)
	rows = frappe.get_all("Sales Order", filters={"name": ["in", sales_orders]}, fields=fields)
	out = {}
	for r in rows:
		out[r.name] = (r.get(CUSTOM_DELIVERY_DATE_FIELD) if has_custom else None) or r.delivery_date or r.transaction_date
	return out


def _so_reserved_map(fg_items):
	"""{item_code: qty reserved against Sales Orders in STOCK_WAREHOUSE}. Only
	Stock Reservation Entries with voucher_type = 'Sales Order' - non-SO
	reservations (production/pick-list/etc.) are excluded from Item Free Stock."""
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
