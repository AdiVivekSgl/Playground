"""
Layers two custom statuses on top of Sales Order's own status:

  Inspected           - custom_inspection_report has an attachment
  Ready for Dispatch  - every line's Stock Reservation Entry qty (in
                        STOCK_WAREHOUSE, the same scope used throughout this
                        app) equals its pending qty (qty - delivered_qty)

Only applied while the SO's own (ERPNext) status is To Deliver / To Deliver
and Bill - never overrides Completed, Closed, Cancelled, Draft, On Hold, or
To Bill. If both conditions hold, Inspected wins.

IMPORTANT - two call paths, both needed:
  1. The `doc_events` "on_update" hook (see hooks.py) catches a normal Sales
     Order save (e.g. a user manually attaches custom_inspection_report and
     saves the form).
  2. ERPNext frequently recomputes Sales Order status via
     `doc.set_status(update=True)` - a lightweight call that does its own
     db_set and does NOT go through a full validate()/on_update save cycle.
     Creating or cancelling a Stock Reservation Entry (see
     fg_stock_reservation_manager.py's create_reservations/
     cancel_reservations) never puts the Sales Order itself through a full
     save, so on_update alone would NEVER catch a reservation-driven change -
     that code calls recompute_for_sales_orders() directly, right after the
     reservation action, instead of relying on this hook to fire.

set_custom_status() itself always calls doc.set_status(update=True) FIRST -
the exact same call ERPNext makes internally from those other trigger
points - before evaluating our own condition. This does double duty:
  - it guarantees we're layering on top of a FRESH base status, not a stale
    one that might already be one of our own custom values from a previous
    pass;
  - it naturally handles "un-setting" too - if a reservation was cancelled and
    our condition no longer holds, ERPNext's own recompute reverts the status
    to whatever it naturally should be (e.g. back to "To Deliver"), since our
    override is never reapplied when the condition fails.

IMPORTANT: "Ready for Dispatch" and "Inspected" are added to OPEN_SO_STATUSES
in production_requirement_report.py so Sales Orders showing these statuses
don't silently disappear from the Production Requirement Report, FG Stock
Reservation Manager, Weekly Planning Snapshot, or JIT Production Planning
Report - all of which filter Sales Orders by that same open-status list.
"""

import frappe
from frappe.utils import flt

ELIGIBLE_BASE_STATUSES = {"To Deliver", "To Deliver and Bill"}
STATUS_INSPECTED = "Inspected"
STATUS_READY_FOR_DISPATCH = "Ready for Dispatch"


def set_custom_status(doc, method=None):
	if doc.docstatus != 1:
		return

	# `status` doesn't have allow_on_submit on this site, so a full validated
	# update on this already-submitted doc (triggered internally by
	# set_status()/db_set() in some code paths) would otherwise throw "Not
	# allowed to change ... after submission". This is the officially
	# supported way to suppress that specific guard for this one in-memory
	# doc/operation - it does NOT disable the submit-lock for anything else,
	# including other fields or other saves of this same document.
	doc.flags.ignore_validate_update_after_submit = True

	# Recompute ERPNext's own status first - see module docstring for why
	# this matters (both "fresh base" and "un-setting" depend on it).
	doc.set_status(update=True)
	base_status = doc.status

	if base_status not in ELIGIBLE_BASE_STATUSES:
		return

	# Deferred import: fg_stock_reservation_manager imports get_line_reserved_map
	# from here isn't the case (it's the other way round - this module needs
	# get_line_reserved_map FROM fg_stock_reservation_manager), so importing it
	# at call time, not module load time, avoids a circular import between the
	# two modules (fg_stock_reservation_manager also imports THIS module's
	# recompute_for_sales_orders, at call time, for the same reason).
	from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
		get_line_reserved_map,
	)

	if doc.get("custom_inspection_report"):
		custom_status = STATUS_INSPECTED
	elif _all_lines_fully_reserved(doc, get_line_reserved_map):
		custom_status = STATUS_READY_FOR_DISPATCH
	else:
		custom_status = None

	if custom_status and doc.status != custom_status:
		doc.db_set("status", custom_status, update_modified=False)


def recompute_for_sales_orders(sales_orders):
	"""Force-recompute status (core + custom layer) for the given Sales
	Orders. Call this directly after any action that changes Stock
	Reservation Entries - such actions don't reliably trigger the Sales
	Order's own on_update doc_event (see module docstring)."""
	for so in {s for s in sales_orders if s}:
		try:
			set_custom_status(frappe.get_doc("Sales Order", so))
		except Exception:
			frappe.log_error(title="sales_order_status.recompute_for_sales_orders: {0}".format(so))


def _all_lines_fully_reserved(doc, get_line_reserved_map):
	"""True if every SO Item line with pending qty > 0 has reserved qty
	(Stock Reservation Entry, STOCK_WAREHOUSE) exactly equal to its pending
	qty - i.e. nothing left to reserve on this order. False if there are no
	pending lines at all (fully delivered already isn't "ready to dispatch")."""
	pending_items = [d for d in doc.items if flt(d.qty) - flt(d.delivered_qty) > 0.0001]
	if not pending_items:
		return False

	reserved_map = get_line_reserved_map([d.name for d in pending_items])
	for d in pending_items:
		pending = flt(d.qty) - flt(d.delivered_qty)
		reserved = flt((reserved_map.get(d.name) or {}).get("reserved_qty"))
		if abs(pending - reserved) > 0.0001:
			return False
	return True
