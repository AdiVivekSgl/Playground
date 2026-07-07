"""
Layers two custom statuses on top of Sales Order's own status, via the
doc_events hook (see hooks.py) rather than a monkey-patch: ERPNext's core
set_status() logic runs first (inside validate()); this hook runs on
on_update, after the save has already gone through, and overrides the
persisted status field directly with db_set (no recursive validate/save).

  Inspected           - custom_inspection_report has an attachment
  Ready for Dispatch  - every line's Stock Reservation Entry qty (in
                        STOCK_WAREHOUSE, the same scope used throughout this
                        app) equals its pending qty (qty - delivered_qty)

Only applied while the SO is still in a pre-dispatch state (To Deliver /
To Deliver and Bill) - never overrides Completed, Closed, Cancelled, Draft,
On Hold, or To Bill. If both conditions hold, Inspected wins.

IMPORTANT: "Ready for Dispatch" and "Inspected" are added to OPEN_SO_STATUSES
in production_requirement_report.py so Sales Orders showing these statuses
don't silently disappear from the Production Requirement Report, FG Stock
Reservation Manager, Weekly Planning Snapshot, or JIT Production Planning
Report - all of which filter Sales Orders by that same open-status list.
"""

import frappe
from frappe.utils import flt

from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
	get_line_reserved_map,
)

ELIGIBLE_BASE_STATUSES = {"To Deliver", "To Deliver and Bill"}
STATUS_INSPECTED = "Inspected"
STATUS_READY_FOR_DISPATCH = "Ready for Dispatch"


def set_custom_status(doc, method=None):
	if doc.docstatus != 1 or doc.status not in ELIGIBLE_BASE_STATUSES:
		return

	if doc.get("custom_inspection_report"):
		custom_status = STATUS_INSPECTED
	elif _all_lines_fully_reserved(doc):
		custom_status = STATUS_READY_FOR_DISPATCH
	else:
		custom_status = None

	if custom_status and doc.status != custom_status:
		doc.db_set("status", custom_status, update_modified=False)


def _all_lines_fully_reserved(doc):
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
