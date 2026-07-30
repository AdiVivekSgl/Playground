# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Scheduled BOM cost refresh
==========================

Backs the monthly ``cron`` scheduler event in hooks.py. On the 1st of every
month this fires an "Update Cost" across every Bill of Materials - the exact
same operation as the "Update Cost" button on ERPNext's BOM Update Tool.

ERPNext processes the BOMs bottom-up, level by level, in its own background
job(s), so raw-material rate changes cascade up into sub-assembly and
finished-good BOM valuations without anyone having to click the tool by hand.
"""

import frappe


def update_cost_for_all_boms():
	"""Enqueue an "Update Cost" run for all BOMs (called by the scheduler).

	Delegates to ERPNext's own BOM Update Tool entry point - the same
	whitelisted method the UI button calls - so we inherit its batching,
	level ordering and BOM Update Log audit trail rather than reimplementing
	the recost loop here.
	"""
	from erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool import (
		enqueue_update_cost,
	)

	enqueue_update_cost()
	frappe.logger().info("playground: monthly BOM 'Update Cost' enqueued for all BOMs")
