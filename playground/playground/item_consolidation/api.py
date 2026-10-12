# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Item Consolidation - dry run, background job, retry
===================================================

    preview(name)            dry run: per-pair blockers, warnings, stock, BOMs
    enqueue(doc)             called from on_submit; queues run_consolidation
    run_consolidation(name)  the job: each pending pair in its own savepoint,
                             committed per pair so progress survives a crash
    retry_failed(name)       re-queues the pairs that failed

See playground/playground/item_consolidation/README.md for the why.
"""

import frappe
from frappe import _
from frappe.utils import cint

from playground.playground import bom_cost
from playground.playground.item_consolidation import actions, checks

JOB = "playground.playground.item_consolidation.api.run_consolidation"
ROW_DOCTYPE = "Item Consolidation Pair"


@frappe.whitelist()
def preview(name):
	doc = frappe.get_doc("Item Consolidation", name)
	doc.check_permission("read")
	return [_pair_report(doc, row) for row in doc.items]


@frappe.whitelist()
def retry_failed(name):
	doc = frappe.get_doc("Item Consolidation", name)
	doc.check_permission("submit")
	if doc.docstatus != 1:
		frappe.throw(_("Only a submitted Item Consolidation can be retried."))
	if doc.status in ("Queued", "In Progress"):
		frappe.throw(_("This Item Consolidation is already {0}.").format(doc.status))
	failed = [r for r in doc.items if r.status == "Failed"]
	if not failed:
		frappe.throw(_("No failed pairs to retry."))
	for row in failed:
		frappe.db.set_value(ROW_DOCTYPE, row.name, {"status": "Pending", "error": None})
	enqueue(doc)
	return {"queued": len(failed)}


def enqueue(doc):
	doc.db_set("status", "Queued")
	frappe.enqueue(
		JOB,
		queue="long",
		timeout=3600,
		enqueue_after_commit=True,
		name=doc.name,
		user=frappe.session.user,
	)


def run_consolidation(name, user=None):
	"""Background job. Runs as ``user`` and re-checks permission (it skipped the endpoint)."""
	if user and user != frappe.session.user:
		frappe.set_user(user)

	doc = frappe.get_doc("Item Consolidation", name)
	doc.check_permission("submit")
	doc.db_set("status", "In Progress")
	frappe.db.commit()

	boms_changed = False
	for row in doc.items:
		if row.status == "Done":
			continue
		savepoint = "item_consolidation_pair"
		frappe.db.savepoint(savepoint)
		try:
			result = consolidate_pair(doc, row)
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			frappe.local.message_log = []
			frappe.log_error(title=_("Item Consolidation {0}: {1} failed").format(doc.name, row.old_item))
			frappe.db.set_value(ROW_DOCTYPE, row.name, {"status": "Failed", "error": _strip_html(str(e))})
		else:
			frappe.db.set_value(ROW_DOCTYPE, row.name, dict(result, status="Done", error=None))
			boms_changed = boms_changed or bool(result["boms_updated"])
		frappe.db.commit()

	statuses = frappe.get_all(ROW_DOCTYPE, filters={"parent": doc.name}, pluck="status")
	doc.db_set("status", overall_status(statuses))
	if boms_changed and cint(doc.update_bom_costs):
		bom_cost.update_cost_for_all_boms()
	frappe.db.commit()
	doc.notify_update()


def consolidate_pair(doc, row):
	"""Run every enabled step for one pair; raises (rolling the pair back) on a blocker."""
	ctx = checks.check_pair(
		row.old_item, row.keep_item, doc.company, disable_old_items=cint(doc.disable_old_items)
	)
	if ctx.blockers:
		frappe.throw("\n".join(ctx.blockers), title=_("Blocked"))

	stock_entries = actions.move_stock(
		row.old_item, row.keep_item, doc.company, ctx.stock, ctx.old.stock_uom, doc.name
	)
	masters = actions.copy_masters(row.old_item, row.keep_item) if cint(doc.copy_masters) else []
	boms_updated, boms_skipped = (
		actions.replace_in_boms(row.old_item, row.keep_item, ctx.boms)
		if cint(doc.replace_in_boms)
		else ([], [])
	)
	if cint(doc.disable_old_items):
		actions.disable_item(row.old_item, row.keep_item, doc.name)

	notes = list(ctx.warnings) + [_("BOM not updated - {0}").format(s) for s in boms_skipped]
	return {
		"stock_entries": ", ".join(stock_entries),
		"boms_updated": ", ".join(boms_updated),
		"masters_updated": "\n".join(masters),
		"notes": "\n".join(notes),
	}


def overall_status(row_statuses):
	if row_statuses and all(s == "Done" for s in row_statuses):
		return "Completed"
	if any(s == "Done" for s in row_statuses):
		return "Partly Failed"
	return "Failed"


def _pair_report(doc, row):
	ctx = checks.check_pair(
		row.old_item, row.keep_item, doc.company, disable_old_items=cint(doc.disable_old_items)
	)
	return {
		"idx": row.idx,
		"old_item": row.old_item,
		"keep_item": row.keep_item,
		"blockers": ctx.blockers,
		"warnings": ctx.warnings,
		"stock": [
			{"warehouse": s.warehouse, "qty": s.actual_qty, "value": s.stock_value} for s in ctx.stock
		],
		"boms": [b.bom for b in ctx.boms] if cint(doc.replace_in_boms) else [],
	}


def _strip_html(text):
	return frappe.utils.strip_html(text or "")[:1000]
