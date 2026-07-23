# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Sales Order — Material Status
=============================

Computes Sales Order.custom_material_status: a single at-a-glance operational
read on where an order stands. One field, one value, resolved by a fixed
precedence chain (first match wins):

  1. Reserved          every open line fully reserved (short-to-complete == 0)
  2. Needs Attention   delivery_date_revision_count > 4, OR custom_updated_delivery_date
                       slipped more than 21 days past the header delivery_date
  3. Available         coverable from free stock AT THE SO'S OWN DISPATCH PRIORITY:
                       after every earlier-dated SO competing for the same items
                       takes its share of free stock, this SO's every line is still
                       fully covered
  4. Possible to Push  coverable in isolation but NOT Available - it could be made
                       Available by delaying SOs scheduled for an earlier date
  5. Reprioritized     not fully reserved AND has cancelled Stock Reservation
                       Entries (reservations were placed, then cancelled)
  6. Planning Pending  no submitted Weekly Planning Snapshot approved after the SO's
                       transaction_date covers all of the SO's lines

If none match, the field is left BLANK - we don't invent a status. Treat a blank
Material Status as "no actionable signal from these six rules".

Ranks 1 & 4 reuse compute_so_qualification_flags() (`ready` / `coverable`) - the
exact helper FGSRM's view filters use. Rank 3 reuses compute_priority_availability(),
the same FIFO-by-dispatch-date waterfall the Production Plan builder runs, so the
stored field and the live reports can never disagree. Free stock uses FGSRM's
formula but nets out ONLY reservations tied to open Sales Orders (actual -
reservations_toward_open_SOs in STOCK_WAREHOUSE), NOT Bin.reserved_qty - so a stray
reservation against a non-open SO can't shrink the pool the open SOs share. This
matches the reserved side of the allocation, which is also open-SO-only.

Because Available / Possible to Push depend on the WHOLE set of SOs competing for an
item, a single SO can't be resolved in isolation. Every compute funnels through
_resolve_statuses(so_items) over a competing universe:
  - Scheduled hourly full recompute: the entire open-SO set (allocation is exact).
  - Targeted events (SO save, reservation submit/cancel, snapshot submit): the
    "item cluster" - every open SO sharing an FG item with the changed SO - since a
    reservation / dispatch-date change reshuffles same-item peers' availability.

Scope: submitted Sales Orders only (docstatus = 1). The "Inspected" state and its
custom_inspection_completed flag were removed from the chain (the flag is retained
but no longer drives status).
"""

import frappe
from frappe.utils import add_days, flt, getdate

from playground.playground.report.production_requirement_report.production_requirement_report import (
	CUSTOM_DELIVERY_DATE_FIELD,
	compute_priority_availability,
	compute_so_qualification_flags,
	get_open_so_items,
	get_reserved_in_stock_warehouse_map,
	get_reserved_map,
	get_stock_map,
	_get_so_header_map,
)
from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
	get_line_reserved_map,
)

MATERIAL_STATUS_FIELD = "custom_material_status"
REVISION_COUNT_FIELD = "delivery_date_revision_count"

# Revision count above which an order is flagged Needs Attention.
REVISION_ATTENTION_THRESHOLD = 4
# Days the updated (dispatch-priority) delivery date may slip past the header
# delivery_date before the order is flagged Needs Attention, independent of the
# revision count.
DELIVERY_DATE_SLIP_DAYS = 21


# --------------------------------------------------------------------------- #
# Free stock (open-SO reservations only) - shared by every compute path
# --------------------------------------------------------------------------- #

def _item_free_stock_map(item_codes, sales_orders):
	"""{item_code: actual_qty - reserved_toward_open_SOs} in STOCK_WAREHOUSE.

	Same free-stock / Suggested-Prodn formula FGSRM uses, but nets out ONLY the
	reservations tied to the open Sales Orders in `sales_orders`
	(get_reserved_in_stock_warehouse_map) - i.e. FGSRM's "Only Displayed SOs" basis
	with the open-SO universe as the displayed set - rather than Bin.reserved_qty
	("All Reservations"). This stops an active/stale reservation against a NON-open
	SO from silently shrinking the free pool the priority allocation shares among
	open SOs. In the healthy case (no reservations against non-open SOs) the two
	bases are identical.

	The demand side (demand = pending - reserved) is likewise open-SO-only
	(get_reserved_map / get_line_reserved_map filter voucher_no to the open SOs), so
	free stock and demand net exactly the same reservation universe."""
	stock_map = get_stock_map(list(item_codes))
	reserved_open = get_reserved_in_stock_warehouse_map(list(sales_orders))
	out = {}
	for item in item_codes:
		stock = stock_map.get(item) or frappe._dict()
		out[item] = flt(stock.get("actual_qty")) - flt(reserved_open.get(item, 0.0))
	return out


# --------------------------------------------------------------------------- #
# Reprioritized (cancelled-reservation heuristic)
# --------------------------------------------------------------------------- #

def _cancelled_sre_sos(sales_orders):
	"""Set of SOs with at least one CANCELLED (docstatus 2) Stock Reservation Entry
	- the signal that reservations were placed and later cancelled. Heuristic basis
	for "Reprioritized" (it can't prove the SO was ever FULLY reserved, only that
	some reservation existed and was cancelled)."""
	if not sales_orders or not frappe.db.table_exists("Stock Reservation Entry"):
		return set()
	rows = frappe.db.sql(
		"""
		SELECT DISTINCT sre.voucher_no AS sales_order
		FROM `tabStock Reservation Entry` sre
		WHERE sre.docstatus = 2
			AND sre.voucher_type = 'Sales Order'
			AND sre.voucher_no IN %(sos)s
		""",
		{"sos": list(sales_orders)},
		as_dict=True,
	)
	return {r.sales_order for r in rows}


# --------------------------------------------------------------------------- #
# Planning Pending
# --------------------------------------------------------------------------- #

def _covered_lines_by_so(sales_orders):
	"""{sales_order: {sales_order_item, ...}} covered by a SUBMITTED Weekly
	Planning Snapshot whose approved_on is later than that SO's transaction_date.
	One set-based query for a whole batch; matches the Review report's
	docstatus = 1 filter on snapshots."""
	if not sales_orders or not frappe.db.table_exists("Weekly Planning Snapshot Item"):
		return {}
	rows = frappe.db.sql(
		"""
		SELECT wpsi.sales_order, wpsi.sales_order_item
		FROM `tabWeekly Planning Snapshot Item` wpsi
		INNER JOIN `tabWeekly Planning Snapshot` wps ON wps.name = wpsi.parent
		INNER JOIN `tabSales Order` so ON so.name = wpsi.sales_order
		WHERE wps.docstatus = 1
			AND wps.approved_on > so.transaction_date
			AND wpsi.sales_order IN %(sos)s
		""",
		{"sos": list(sales_orders)},
		as_dict=True,
	)
	out = {}
	for r in rows:
		out.setdefault(r.sales_order, set()).add(r.sales_order_item)
	return out


def _is_planning_pending(so_item_names, covered_lines):
	"""True when not every one of the SO's open lines is covered by a qualifying
	snapshot. An SO with no open lines is vacuously covered (not pending)."""
	if not so_item_names:
		return False
	return not set(so_item_names).issubset(covered_lines or set())


# --------------------------------------------------------------------------- #
# Precedence chain (pure) - the single definition of the ordering
# --------------------------------------------------------------------------- #

def _resolve_material_status(header, so_flags, available, has_cancelled_sre, planning_pending):
	"""Apply the fixed precedence to already-gathered inputs. Returns the status
	string, or None when nothing matches (field left blank - see module docstring)."""
	# 1. Reserved
	if so_flags.get("ready"):
		return "Reserved"

	# 2. Needs Attention - too many delivery-date revisions, OR the updated
	#    (dispatch-priority) delivery date has slipped more than 21 days past the
	#    header delivery_date.
	updated_date = header.get(CUSTOM_DELIVERY_DATE_FIELD)
	delivery_date = header.get("delivery_date")
	slipped = bool(
		updated_date
		and delivery_date
		and getdate(updated_date) > getdate(add_days(delivery_date, DELIVERY_DATE_SLIP_DAYS))
	)
	if (header.get(REVISION_COUNT_FIELD) or 0) > REVISION_ATTENTION_THRESHOLD or slipped:
		return "Needs Attention"

	# 3. Available (covered after priority allocation)
	if available:
		return "Available"

	# 4. Possible to Push (coverable in isolation but blocked by earlier-dated SOs)
	if so_flags.get("coverable"):
		return "Possible to Push"

	# 5. Reprioritized (reservations placed then cancelled, no longer covered)
	if has_cancelled_sre:
		return "Reprioritized"

	# 6. Planning Pending
	if planning_pending:
		return "Planning Pending"

	return None


def _header_fields():
	"""Sales Order header columns the resolver reads (Needs Attention needs the
	revision count + both delivery dates), guarding custom fields with has_column so
	this runs on a site missing them. delivery_date is a standard header field."""
	fields = ["name", "delivery_date"]
	for f in (REVISION_COUNT_FIELD, CUSTOM_DELIVERY_DATE_FIELD):
		if frappe.db.has_column("Sales Order", f):
			fields.append(f)
	return fields


def _so_header(sales_order):
	"""Header for the single-SO entry point: identity + docstatus, plus the fields
	the resolver reads (revision count + both delivery dates) when present."""
	fields = ["name", "docstatus", "delivery_date"]
	for f in (REVISION_COUNT_FIELD, CUSTOM_DELIVERY_DATE_FIELD):
		if frappe.db.has_column("Sales Order", f):
			fields.append(f)
	return frappe.db.get_value("Sales Order", sales_order, fields, as_dict=True) or frappe._dict()


# --------------------------------------------------------------------------- #
# Shared resolve + persist engine (over a competing universe of SO items)
# --------------------------------------------------------------------------- #

def _resolve_statuses(so_items):
	"""Resolve (do NOT persist) Material Status for every SO represented in
	so_items. so_items MUST be the full competing universe for those SOs' items -
	priority availability needs same-item peers - so callers pass either the whole
	open-SO set (hourly) or an item cluster (targeted). Builds all shared maps
	once. Returns {sales_order: status or None}."""
	if not so_items:
		return {}

	sales_orders = list(dict.fromkeys(r.sales_order for r in so_items))
	item_codes = {r.item_code for r in so_items}

	line_reserved = get_line_reserved_map([r.so_item for r in so_items])
	item_free = _item_free_stock_map(item_codes, sales_orders)
	flags = compute_so_qualification_flags(so_items, line_reserved, item_free)

	reserved_map = get_reserved_map(sales_orders)
	so_sort_date = {so: h.get("sort_date") for so, h in _get_so_header_map(sales_orders).items()}
	available_map = compute_priority_availability(so_items, reserved_map, item_free, so_sort_date)

	cancelled_sos = _cancelled_sre_sos(sales_orders)
	covered_by_so = _covered_lines_by_so(sales_orders)

	items_by_so = {}
	for r in so_items:
		items_by_so.setdefault(r.sales_order, []).append(r.so_item)

	headers = {
		h.name: h
		for h in frappe.get_all(
			"Sales Order", filters={"name": ["in", sales_orders]}, fields=_header_fields()
		)
	}

	out = {}
	for so in sales_orders:
		header = headers.get(so) or frappe._dict()
		so_flags = flags.get(so, {"ready": True, "coverable": True})
		planning_pending = _is_planning_pending(items_by_so.get(so, []), covered_by_so.get(so, set()))
		out[so] = _resolve_material_status(
			header,
			so_flags,
			available_map.get(so, True),
			so in cancelled_sos,
			planning_pending,
		)
	return out


def _write_status(sales_order, status):
	"""Persist a resolved status, writing only on a real change. Uses db.set_value
	(update_modified=False) so it doesn't churn the modified timestamp or re-fire
	on_update. No-op if the field isn't installed on this site."""
	if not frappe.db.has_column("Sales Order", MATERIAL_STATUS_FIELD):
		return
	status = status or ""
	current = frappe.db.get_value("Sales Order", sales_order, MATERIAL_STATUS_FIELD) or ""
	if status != current:
		frappe.db.set_value(
			"Sales Order", sales_order, MATERIAL_STATUS_FIELD, status, update_modified=False
		)


def _persist_statuses(so_items):
	"""Resolve the competing universe and persist every changed value."""
	for so, status in _resolve_statuses(so_items).items():
		_write_status(so, status)


# --------------------------------------------------------------------------- #
# Public single-SO compute (introspection) + entry points
# --------------------------------------------------------------------------- #

def compute_material_status(sales_order):
	"""Correct single-SO status (no persist). Expands to the SO's item cluster so
	priority availability sees competing peers, then returns THIS SO's status (or
	None). A submitted SO with no open lines is vacuously Reserved."""
	header = _so_header(sales_order)
	if header.get("docstatus") != 1:
		return None
	own = get_open_so_items({"sales_order": sales_order})
	if not own:
		return _resolve_material_status(
			header,
			{"ready": True, "coverable": True},
			True,
			sales_order in _cancelled_sre_sos([sales_order]),
			False,
		)
	so_items = get_open_so_items({"item_codes": list({r.item_code for r in own})})
	return _resolve_statuses(so_items).get(sales_order)


def _recompute_cluster(sales_order):
	"""Persist Material Status for `sales_order` AND every open SO sharing an FG
	item with it - a reservation / dispatch-date change reshuffles same-item peers'
	priority availability. No-op if the field isn't installed."""
	if not frappe.db.has_column("Sales Order", MATERIAL_STATUS_FIELD):
		return
	own = get_open_so_items({"sales_order": sales_order})
	if not own:
		# No open lines: nothing competes; resolve this SO alone.
		_write_status(sales_order, compute_material_status(sales_order))
		return
	so_items = get_open_so_items({"item_codes": list({r.item_code for r in own})})
	_persist_statuses(so_items)


def recompute_all_open_so_material_status():
	"""Scheduled hourly pass. Fetches the entire open-SO universe ONCE and resolves
	every SO against those shared maps - so priority availability is exact and there
	is no N+1."""
	if not frappe.db.has_column("Sales Order", MATERIAL_STATUS_FIELD):
		return
	so_items = get_open_so_items({})  # docstatus = 1, open statuses, pending > 0
	if not so_items:
		return
	_persist_statuses(so_items)
	frappe.db.commit()


# --------------------------------------------------------------------------- #
# Sales Order controller hooks + doc-event recompute triggers
# --------------------------------------------------------------------------- #

def on_sales_order_validate(doc, method=None):
	"""Delivery-date revision counter - Sales-form / controller saves ONLY. This
	is intentional: FGSRM's update_dispatch_priority_date writes via
	frappe.db.set_value and bypasses validate entirely, so bulk reprioritization
	from that screen never inflates this counter."""
	if doc.is_new():
		return
	if not doc.meta.has_field(REVISION_COUNT_FIELD) or not doc.meta.has_field(CUSTOM_DELIVERY_DATE_FIELD):
		return
	before = doc.get_doc_before_save()
	if not before:
		return
	if before.get(CUSTOM_DELIVERY_DATE_FIELD) != doc.get(CUSTOM_DELIVERY_DATE_FIELD):
		doc.set(REVISION_COUNT_FIELD, (doc.get(REVISION_COUNT_FIELD) or 0) + 1)


def on_sales_order_update(doc, method=None):
	"""Targeted cluster recompute after a save that could change status: submit, a
	dispatch-date change (reshuffles priority availability), or a revision-count
	change (Needs Attention). Unrelated header edits are skipped."""
	if doc.docstatus != 1:
		return
	before = doc.get_doc_before_save()
	changed = (
		before is None
		or before.docstatus != doc.docstatus
		or before.get(CUSTOM_DELIVERY_DATE_FIELD) != doc.get(CUSTOM_DELIVERY_DATE_FIELD)
		or (before.get(REVISION_COUNT_FIELD) or 0) != (doc.get(REVISION_COUNT_FIELD) or 0)
	)
	if changed:
		_recompute_cluster(doc.name)


def recompute_from_sre(doc, method=None):
	"""Stock Reservation Entry submit/cancel -> recompute the SO's item cluster
	(the reservation change moves free stock for every same-item peer)."""
	if doc.get("voucher_type") == "Sales Order" and doc.get("voucher_no"):
		_recompute_cluster(doc.voucher_no)


def recompute_from_snapshot(doc, method=None):
	"""Weekly Planning Snapshot submit -> recompute each referenced SO's cluster."""
	for so in {d.sales_order for d in doc.get("items", []) if d.get("sales_order")}:
		_recompute_cluster(so)
