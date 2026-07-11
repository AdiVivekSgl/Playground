# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Sales Order — Material Status
=============================

Computes Sales Order.custom_material_status: a single at-a-glance operational
read on where an order stands. One field, one value, resolved by a fixed
precedence chain (first match wins):

  1. Reprioritize      custom_updated_delivery_date > today+15d AND any line reserved
  2. Inspected         custom_inspection_completed is checked
  3. Ready to Dispatch  every line fully reserved (short-to-complete == 0)
  4. Needs Attention   delivery_date_revision_count > 4
  5. Possible to Push  every line's shortfall coverable by free stock (and not Ready)
  6. Planning Pending  no submitted Weekly Planning Snapshot approved after the SO's
                       transaction_date covers all of the SO's lines

If none match (e.g. planning is done but the order is still short and not
coverable from stock), the field is left BLANK - we don't invent a status. On
real data this residual state should be rare; treat a blank Material Status as
"no actionable signal from these six rules".

Ranks 3 & 5 reuse compute_so_qualification_flags() - the exact same helper
FGSRM's "Ready to Dispatch" / "Possible to Complete" view filters use - so the
stored field and the live report can never disagree. Free stock is measured on
the "All Reservations" basis (actual - Bin.reserved_qty), the conservative,
filter-independent choice, since this field has no toggle UI.

Scope: submitted Sales Orders only (docstatus = 1). Revision-counting and
"since submission" only make sense post-submit, so Drafts are deliberately
excluded from the shared open-SO universe here.

Triggers (hooks.py):
  - Scheduled hourly full recompute (one batched pass over every open SO).
  - Targeted recompute on the events that can change a status: Sales Order save,
    Stock Reservation Entry submit/cancel, Weekly Planning Snapshot submit.

This is the app's first doc_events / scheduler_events infrastructure.
"""

import frappe
from frappe.utils import add_days, flt, getdate, nowdate

from playground.playground.report.production_requirement_report.production_requirement_report import (
	CUSTOM_DELIVERY_DATE_FIELD,
	compute_so_qualification_flags,
	get_open_so_items,
	get_stock_map,
)
from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
	get_line_reserved_map,
)

MATERIAL_STATUS_FIELD = "custom_material_status"
INSPECTION_FIELD = "custom_inspection_completed"
REVISION_COUNT_FIELD = "delivery_date_revision_count"

# Days past today at/under which a far-out delivery date triggers Reprioritize.
REPRIORITIZE_HORIZON_DAYS = 15
# Revision count above which an order is flagged Needs Attention.
REVISION_ATTENTION_THRESHOLD = 4


# --------------------------------------------------------------------------- #
# Free stock (All Reservations basis) - shared by single + batch paths
# --------------------------------------------------------------------------- #

def _item_free_stock_map(item_codes):
	"""{item_code: actual_qty - Bin.reserved_qty} in STOCK_WAREHOUSE, i.e. the
	"All Reservations" basis (nets out every reservation, not just displayed
	SOs). Fixed here because the field has no basis toggle."""
	stock_map = get_stock_map(list(item_codes))
	out = {}
	for item in item_codes:
		stock = stock_map.get(item) or frappe._dict()
		out[item] = flt(stock.get("actual_qty")) - flt(stock.get("reserved_qty"))
	return out


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

def _resolve_material_status(header, so_flags, has_reservation, planning_pending):
	"""Apply the fixed precedence to already-gathered inputs. Returns the status
	string, or None when nothing matches (field left blank - see module docstring)."""
	# 1. Reprioritize
	far_date = header.get(CUSTOM_DELIVERY_DATE_FIELD)
	if far_date and has_reservation:
		if getdate(far_date) > getdate(add_days(nowdate(), REPRIORITIZE_HORIZON_DAYS)):
			return "Reprioritize"

	# 2. Inspected
	if header.get(INSPECTION_FIELD):
		return "Inspected"

	# 3. Ready to Dispatch
	if so_flags.get("ready"):
		return "Ready to Dispatch"

	# 4. Needs Attention
	if (header.get(REVISION_COUNT_FIELD) or 0) > REVISION_ATTENTION_THRESHOLD:
		return "Needs Attention"

	# 5. Possible to Push (coverable and, implicitly, not Ready - Ready returned above)
	if so_flags.get("coverable"):
		return "Possible to Push"

	# 6. Planning Pending
	if planning_pending:
		return "Planning Pending"

	return None


def _so_header(sales_order):
	"""Header fields the precedence chain reads, guarding each custom field with
	has_column so this runs on a site missing any of them."""
	fields = ["name", "docstatus", "transaction_date"]
	for f in (CUSTOM_DELIVERY_DATE_FIELD, INSPECTION_FIELD, REVISION_COUNT_FIELD):
		if frappe.db.has_column("Sales Order", f):
			fields.append(f)
	return frappe.db.get_value("Sales Order", sales_order, fields, as_dict=True) or frappe._dict()


# --------------------------------------------------------------------------- #
# Single-SO compute (targeted hooks) + batch compute (scheduled)
# --------------------------------------------------------------------------- #

def compute_material_status(sales_order):
	"""Resolve Material Status for one submitted Sales Order. Returns the status
	string or None. Builds only this SO's maps (fine for a targeted recompute;
	the scheduled pass batches instead - see recompute_all_open_so_material_status)."""
	header = _so_header(sales_order)
	if header.get("docstatus") != 1:
		# Drafts / cancelled are out of scope - no status.
		return None

	so_items = get_open_so_items({"sales_order": sales_order})
	so_item_names = [r.so_item for r in so_items]
	line_reserved = get_line_reserved_map(so_item_names)
	item_free = _item_free_stock_map({r.item_code for r in so_items})

	flags = compute_so_qualification_flags(so_items, line_reserved, item_free)
	# No open lines -> vacuously ready (nothing left to reserve).
	so_flags = flags.get(sales_order, {"ready": True, "coverable": True})

	has_reservation = any(
		flt((line_reserved.get(r.so_item) or frappe._dict()).get("reserved_qty")) > 0
		for r in so_items
	)

	covered = _covered_lines_by_so([sales_order]).get(sales_order, set())
	planning_pending = _is_planning_pending(so_item_names, covered)

	return _resolve_material_status(header, so_flags, has_reservation, planning_pending)


def _set_material_status(sales_order):
	"""Compute and persist, writing only on a real change. Uses db.set_value
	(update_modified=False) so it doesn't churn the modified timestamp or
	re-fire on_update. No-op if the field isn't installed on this site."""
	if not frappe.db.has_column("Sales Order", MATERIAL_STATUS_FIELD):
		return
	status = compute_material_status(sales_order) or ""
	current = frappe.db.get_value("Sales Order", sales_order, MATERIAL_STATUS_FIELD) or ""
	if status != current:
		frappe.db.set_value(
			"Sales Order", sales_order, MATERIAL_STATUS_FIELD, status, update_modified=False
		)


def recompute_all_open_so_material_status():
	"""Scheduled hourly pass. Fetches the open-SO universe and its stock /
	reservation / snapshot data ONCE, then rolls up per SO against those shared
	maps - avoiding the N+1 that calling compute_material_status() per SO would
	incur."""
	if not frappe.db.has_column("Sales Order", MATERIAL_STATUS_FIELD):
		return

	so_items = get_open_so_items({})  # docstatus = 1, open statuses, pending > 0
	if not so_items:
		return

	sales_orders = list(dict.fromkeys(r.sales_order for r in so_items))
	item_codes = {r.item_code for r in so_items}

	line_reserved = get_line_reserved_map([r.so_item for r in so_items])
	item_free = _item_free_stock_map(item_codes)
	flags = compute_so_qualification_flags(so_items, line_reserved, item_free)
	covered_by_so = _covered_lines_by_so(sales_orders)

	# Per-SO groupings from the single fetch.
	items_by_so = {}
	reserved_sos = set()
	for r in so_items:
		items_by_so.setdefault(r.sales_order, []).append(r.so_item)
		if flt((line_reserved.get(r.so_item) or frappe._dict()).get("reserved_qty")) > 0:
			reserved_sos.add(r.sales_order)

	# Headers in one query.
	header_fields = ["name", CUSTOM_DELIVERY_DATE_FIELD, INSPECTION_FIELD, REVISION_COUNT_FIELD]
	header_fields = [f for f in header_fields if f == "name" or frappe.db.has_column("Sales Order", f)]
	headers = {
		h.name: h
		for h in frappe.get_all("Sales Order", filters={"name": ["in", sales_orders]}, fields=header_fields)
	}

	for so in sales_orders:
		header = headers.get(so) or frappe._dict()
		so_flags = flags.get(so, {"ready": True, "coverable": True})
		so_item_names = items_by_so.get(so, [])
		planning_pending = _is_planning_pending(so_item_names, covered_by_so.get(so, set()))
		status = _resolve_material_status(header, so_flags, so in reserved_sos, planning_pending) or ""

		current = frappe.db.get_value("Sales Order", so, MATERIAL_STATUS_FIELD) or ""
		if status != current:
			frappe.db.set_value("Sales Order", so, MATERIAL_STATUS_FIELD, status, update_modified=False)

	frappe.db.commit()


# --------------------------------------------------------------------------- #
# Sales Order controller hooks
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
	"""Targeted recompute after a save that could change status. Skips unrelated
	saves: only runs on submit or when the inspection flag / delivery date
	actually changed."""
	if doc.docstatus != 1:
		return
	before = doc.get_doc_before_save()
	changed = (
		before is None
		or before.docstatus != doc.docstatus
		or before.get(INSPECTION_FIELD) != doc.get(INSPECTION_FIELD)
		or before.get(CUSTOM_DELIVERY_DATE_FIELD) != doc.get(CUSTOM_DELIVERY_DATE_FIELD)
	)
	if changed:
		_set_material_status(doc.name)


def recompute_from_sre(doc, method=None):
	"""Stock Reservation Entry submit/cancel -> recompute the SO it reserves."""
	if doc.get("voucher_type") == "Sales Order" and doc.get("voucher_no"):
		_set_material_status(doc.voucher_no)


def recompute_from_snapshot(doc, method=None):
	"""Weekly Planning Snapshot submit -> recompute every SO it references."""
	sales_orders = {d.sales_order for d in doc.get("items", []) if d.get("sales_order")}
	for so in sales_orders:
		_set_material_status(so)
