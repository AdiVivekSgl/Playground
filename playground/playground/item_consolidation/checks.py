# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Pre-checks for one old_item -> keep_item pair
=============================================

``check_pair`` gathers everything the consolidation needs to know about a pair
(item flags, stock per warehouse, open documents, BOMs, pending reposts, period
locks) and ``evaluate_pair`` turns that into blockers / warnings. The split keeps
the rules pure so they can be unit-tested without a site.

A blocker stops the pair. Warnings are shown in the dry run and recorded on the
row, but do not stop it.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today

from playground.playground.bom_component_replace import find_affected_boms

ITEM_FIELDS = [
	"name",
	"item_name",
	"disabled",
	"stock_uom",
	"is_stock_item",
	"has_serial_no",
	"has_batch_no",
	"has_variants",
	"valuation_method",
	"default_bom",
]

# (parent doctype, child doctype or None, item field, "still open" condition on p)
# A disabled item cannot be received, delivered or consumed, so these must be
# completed / closed / amended before the old item is disabled.
OPEN_DOCUMENT_SOURCES = [
	("Quotation", "Quotation Item", "item_code", "p.docstatus = 0 or (p.docstatus = 1 and p.status = 'Open')"),
	("Sales Order", "Sales Order Item", "item_code", "p.docstatus = 0 or (p.docstatus = 1 and p.status not in ('Completed', 'Closed'))"),
	("Purchase Order", "Purchase Order Item", "item_code", "p.docstatus = 0 or (p.docstatus = 1 and p.status not in ('Completed', 'Closed', 'Delivered'))"),
	("Material Request", "Material Request Item", "item_code", "p.docstatus = 0 or (p.docstatus = 1 and p.status not in ('Stopped', 'Ordered', 'Issued', 'Transferred', 'Received', 'Manufactured'))"),
	("Work Order", None, "production_item", "p.docstatus = 0 or (p.docstatus = 1 and p.status not in ('Completed', 'Stopped', 'Closed'))"),
	("Work Order", "Work Order Item", "item_code", "p.docstatus = 0 or (p.docstatus = 1 and p.status not in ('Completed', 'Stopped', 'Closed'))"),
	("Stock Entry", "Stock Entry Detail", "item_code", "p.docstatus = 0"),
	("Purchase Receipt", "Purchase Receipt Item", "item_code", "p.docstatus = 0"),
	("Delivery Note", "Delivery Note Item", "item_code", "p.docstatus = 0"),
	("Purchase Invoice", "Purchase Invoice Item", "item_code", "p.docstatus = 0"),
	("Sales Invoice", "Sales Invoice Item", "item_code", "p.docstatus = 0"),
]

OPEN_DOCUMENT_LIMIT = 20


# --------------------------------------------------------------------------- #
# Data gathering
# --------------------------------------------------------------------------- #
def check_pair(old_item, keep_item, company, block_open_documents=True, posting_date=None):
	"""Everything known about one pair, plus its blockers and warnings."""
	old = _get_item(old_item)
	keep = _get_item(keep_item)
	stock = get_stock_rows(old_item, company) if old else []

	context = frappe._dict(
		old=old,
		keep=keep,
		stock=stock,
		other_company_stock=_other_company_stock(old_item, company) if old else [],
		open_documents=get_open_documents(old_item) if old else [],
		boms=_active_boms(old_item) if old else [],
		old_produced_by_boms=_boms_producing(old_item) if old else [],
		pending_reposts=_pending_reposts(old_item) if old else [],
		active_reservations=_active_reservations(old_item) if old else [],
		lock_dates=get_lock_dates(company),
		closed_accounting_period=_closed_accounting_period(company, posting_date or today()),
	)
	posting_date = posting_date or today()
	blockers, warnings = evaluate_pair(
		old_item, keep_item, context, block_open_documents=block_open_documents, posting_date=posting_date
	)
	# True when open documents are the only thing in the way - the form then offers
	# to disable the item anyway instead of just refusing.
	open_documents_only = bool(blockers) and not evaluate_pair(
		old_item, keep_item, context, block_open_documents=False, posting_date=posting_date
	)[0]
	context.update(blockers=blockers, warnings=warnings, open_documents_only=open_documents_only)
	return context


def get_stock_rows(item_code, company):
	"""Non-zero Bins of ``item_code`` in ``company``'s warehouses."""
	return frappe.db.sql(
		"""
		SELECT b.warehouse, b.actual_qty, b.stock_value, b.valuation_rate
		FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
		WHERE b.item_code = %(item)s AND w.company = %(company)s AND b.actual_qty != 0
		ORDER BY b.warehouse
		""",
		{"item": item_code, "company": company},
		as_dict=True,
	)


def get_open_documents(item_code):
	"""[{doctype, names: [...]}] of documents that would break once the item is disabled."""
	out = []
	for parent, child, field, condition in OPEN_DOCUMENT_SOURCES:
		if child:
			query = f"""
				SELECT DISTINCT p.name FROM `tab{parent}` p
				INNER JOIN `tab{child}` c ON c.parent = p.name AND c.parenttype = %(parent)s
				WHERE c.`{field}` = %(item)s AND ({condition})
				ORDER BY p.name LIMIT {OPEN_DOCUMENT_LIMIT}"""
		else:
			query = f"""
				SELECT p.name FROM `tab{parent}` p
				WHERE p.`{field}` = %(item)s AND ({condition})
				ORDER BY p.name LIMIT {OPEN_DOCUMENT_LIMIT}"""
		names = frappe.db.sql_list(query, {"parent": parent, "item": item_code})
		if names:
			existing = next((d for d in out if d["doctype"] == parent), None)
			if existing:
				existing["names"] = sorted(set(existing["names"]) | set(names))
			else:
				out.append({"doctype": parent, "names": names})
	return out


def get_lock_dates(company):
	"""[(label, date)] - every date on/before which stock or accounts are closed."""
	locks = []
	pcv = frappe.db.sql(
		"""SELECT MAX(period_end_date) FROM `tabPeriod Closing Voucher`
		WHERE company = %s AND docstatus = 1""",
		company,
	)
	if pcv and pcv[0][0]:
		locks.append((_("Period Closing Voucher"), pcv[0][0]))

	if frappe.db.exists("DocType", "Closing Stock Balance"):
		csb = frappe.db.sql(
			"""SELECT MAX(to_date) FROM `tabClosing Stock Balance`
			WHERE company = %s AND docstatus = 1""",
			company,
		)
		if csb and csb[0][0]:
			locks.append((_("Closing Stock Balance"), csb[0][0]))

	for doctype, field, label in (
		("Accounts Settings", "acc_frozen_upto", _("Accounts Frozen Till")),
		("Stock Settings", "stock_frozen_upto", _("Stock Frozen Up To")),
	):
		if not frappe.get_meta(doctype).has_field(field):
			continue
		value = frappe.db.get_single_value(doctype, field)
		if value:
			locks.append((label, value))
	return locks


def _get_item(item_code):
	if not item_code:
		return None
	return frappe.db.get_value("Item", item_code, ITEM_FIELDS, as_dict=True)


def _other_company_stock(item_code, company):
	return frappe.db.sql_list(
		"""
		SELECT DISTINCT w.company FROM `tabBin` b
		INNER JOIN `tabWarehouse` w ON w.name = b.warehouse
		WHERE b.item_code = %(item)s AND w.company != %(company)s AND b.actual_qty != 0
		""",
		{"item": item_code, "company": company},
	)


def _active_boms(item_code):
	"""BOMs to update: active (submitted) or draft ones using the item as a component."""
	return [b for b in find_affected_boms(item_code) if b.is_active or b.docstatus == 0]


def _boms_producing(item_code):
	return frappe.get_all(
		"BOM", filters={"item": item_code, "docstatus": 1, "is_active": 1}, pluck="name"
	)


def _pending_reposts(item_code):
	return frappe.get_all(
		"Repost Item Valuation",
		filters={"item_code": item_code, "docstatus": 1, "status": ["in", ["Queued", "In Progress"]]},
		pluck="name",
	)


def _active_reservations(item_code):
	if not frappe.db.exists("DocType", "Stock Reservation Entry"):
		return []
	return frappe.get_all(
		"Stock Reservation Entry",
		filters={
			"item_code": item_code,
			"docstatus": 1,
			"status": ["not in", ["Delivered", "Cancelled"]],
		},
		pluck="name",
	)


def _closed_accounting_period(company, posting_date):
	"""Name of an Accounting Period that closes Stock Entries on ``posting_date``."""
	rows = frappe.db.sql(
		"""
		SELECT ap.name FROM `tabAccounting Period` ap
		INNER JOIN `tabClosed Document` cd ON cd.parent = ap.name
		WHERE ap.company = %(company)s AND %(date)s BETWEEN ap.start_date AND ap.end_date
		  AND cd.document_type = 'Stock Entry' AND cd.closed = 1
		LIMIT 1
		""",
		{"company": company, "date": posting_date},
	)
	return rows[0][0] if rows else None


# --------------------------------------------------------------------------- #
# Rules (pure)
# --------------------------------------------------------------------------- #
def evaluate_pair(old_item, keep_item, ctx, block_open_documents=True, posting_date=None):
	"""Return (blockers, warnings) for a pair given the gathered ``ctx``."""
	blockers, warnings = [], []
	old, keep = ctx.get("old"), ctx.get("keep")

	if not old:
		blockers.append(_("Item {0} does not exist.").format(old_item))
	if not keep:
		blockers.append(_("Item {0} does not exist.").format(keep_item))
	if not old or not keep:
		return blockers, warnings

	if old_item == keep_item:
		blockers.append(_("The item to keep is the same as the item to retire."))
	if cint(keep.disabled):
		blockers.append(_("Item to keep {0} is disabled.").format(keep_item))
	for item in (old, keep):
		if cint(item.has_variants):
			blockers.append(
				_("{0} is a template item (has variants); consolidate its variants instead.").format(item.name)
			)
	if old.stock_uom != keep.stock_uom:
		blockers.append(
			_("Stock UOMs differ ({0}: {1}, {2}: {3}).").format(
				old_item, old.stock_uom, keep_item, keep.stock_uom
			)
		)
	if cint(old.is_stock_item) != cint(keep.is_stock_item):
		blockers.append(_("One item is a stock item and the other is not."))

	stock = ctx.get("stock") or []
	if any(flt(r.actual_qty) < 0 for r in stock):
		blockers.append(_("{0} has negative stock in some warehouses; correct it first.").format(old_item))
	if stock and (cint(old.has_serial_no) or cint(old.has_batch_no)):
		blockers.append(
			_("{0} is batch/serial tracked and has stock; move that stock manually first.").format(old_item)
		)
	if ctx.get("pending_reposts"):
		blockers.append(
			_("Repost Item Valuation still running for {0}: {1}. Wait for it to finish.").format(
				old_item, ", ".join(ctx["pending_reposts"])
			)
		)
	if ctx.get("active_reservations"):
		blockers.append(
			_("{0} has active Stock Reservation Entries: {1}. Unreserve them first.").format(
				old_item, ", ".join(ctx["active_reservations"])
			)
		)

	blockers.extend(lock_blockers(posting_date, ctx.get("lock_dates") or []))
	if ctx.get("closed_accounting_period"):
		blockers.append(
			_("Stock Entries are closed for today in Accounting Period {0}.").format(
				ctx["closed_accounting_period"]
			)
		)

	open_docs = ctx.get("open_documents") or []
	if open_docs:
		summary = "; ".join("{0}: {1}".format(d["doctype"], ", ".join(d["names"])) for d in open_docs)
		if block_open_documents:
			blockers.append(
				_("Open documents use {0} and would fail once it is disabled - complete, close or amend them: {1}").format(
					old_item, summary
				)
			)
		else:
			warnings.append(_("Open documents still use {0}: {1}").format(old_item, summary))

	if (old.valuation_method or "") != (keep.valuation_method or ""):
		warnings.append(
			_("Valuation methods differ; moved stock takes {0}'s method from now on.").format(keep_item)
		)
	if not stock and (
		cint(old.has_batch_no) != cint(keep.has_batch_no) or cint(old.has_serial_no) != cint(keep.has_serial_no)
	):
		warnings.append(_("Batch/serial tracking differs between the two items."))
	if ctx.get("other_company_stock"):
		warnings.append(
			_("{0} also has stock in other companies ({1}); that stock is not moved.").format(
				old_item, ", ".join(ctx["other_company_stock"])
			)
		)
	if ctx.get("old_produced_by_boms"):
		warnings.append(
			_("{0} is manufactured by BOM(s) {1}; those are left unchanged.").format(
				old_item, ", ".join(ctx["old_produced_by_boms"])
			)
		)
		if not keep.default_bom:
			warnings.append(
				_("{0} has no default BOM, so BOM rows switched to it will have no sub-assembly BOM.").format(
					keep_item
				)
			)

	return blockers, warnings


def lock_blockers(posting_date, lock_dates):
	"""Blockers for every closing / freeze date on or after ``posting_date``."""
	posting_date = getdate(posting_date)
	return [
		_("Posting date {0} is on or before {1} ({2}).").format(posting_date, getdate(lock), label)
		for label, lock in lock_dates
		if lock and posting_date <= getdate(lock)
	]


def validate_pair_rows(rows):
	"""Structural checks on the pair table. Returns a list of error strings."""
	errors = []
	if not rows:
		return [_("Add at least one item pair.")]

	keeps = {r.keep_item for r in rows}
	seen = set()
	for r in rows:
		if not r.old_item or not r.keep_item:
			errors.append(_("Row {0}: both items are required.").format(r.idx))
			continue
		if r.old_item == r.keep_item:
			errors.append(_("Row {0}: the item to keep is the same as the item to retire.").format(r.idx))
		if r.old_item in seen:
			errors.append(_("Row {0}: {1} is listed more than once.").format(r.idx, r.old_item))
		seen.add(r.old_item)
		if r.old_item in keeps:
			errors.append(
				_("Row {0}: {1} is also an item to keep in another row; point every row at the final item.").format(
					r.idx, r.old_item
				)
			)
	return errors
