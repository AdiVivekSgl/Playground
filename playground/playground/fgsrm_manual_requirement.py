# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
FGSRM Manual Requirements
=========================

Per-user, manually-added finished-goods demand for the FG Stock Reservation
Manager - either free-form (item + qty) or cherry-picked line-by-line from an
open Blanket Order / Quotation. Persisted in the FGSRM Manual Requirement
doctype so they survive report refreshes and stay until the planner clears them.

Scope: everything here is scoped to the CURRENT USER (the doctype's `owner`).
A planner only ever sees, edits, clears, or plans from their own rows - one
person's speculative additions never leak into another's report or Production
Plan. System Manager can act on any row.

Role in the report (see fg_stock_reservation_manager.py):
  - execute() appends these as rows at the bottom of the report. They carry no
    Sales Order, so the per-line reserve/cancel actions no-op on them (the same
    shape as a "Group by Sales Order" summary row).
  - They are demand only. Stock is never reserved against a Blanket Order /
    Quotation - Stock Reservation Entry supports Sales Order / Work Order voucher
    types only - so the reservation columns are blank for these rows.
  - manual_requirement_qty_by_item() feeds _suggested_prodn_by_item(), so the
    qty flows into Suggested Prodn and Create Prodn Plan as extra demand, netted
    against the free stock left after real Sales Orders.

DELIBERATELY separate from the report module (mirrors fgsrm_dashboard.py): a
plain filters dict in, plain data out, no report/DataTable assumptions.
"""

import frappe
from frappe import _
from frappe.utils import flt

DOCTYPE = "FGSRM Manual Requirement"
# Source document kinds a requirement can be cherry-picked from. These strings
# are exact DocType names, so the doctype's source_document Dynamic Link resolves.
SOURCE_TYPES = ("Blanket Order", "Quotation")


def _owner_filter():
	"""Rows belonging to the current user. System Manager still only sees its own
	in the report (a shared operational screen); it can, however, act on any row
	through the guarded mutators below."""
	return {"owner": frappe.session.user}


def _guard_owner(name):
	"""Load a row and ensure the current user may mutate it: the owner, or a
	System Manager. Throws PermissionError otherwise."""
	doc = frappe.get_doc(DOCTYPE, name)
	if doc.owner != frappe.session.user and "System Manager" not in frappe.get_roles():
		frappe.throw(
			_("You can only change your own manual requirements."), frappe.PermissionError
		)
	return doc


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #

def list_manual_requirements(filters=None):
	"""The current user's manual requirements as display dicts, honouring the
	report's item_code / customer filters (a manual row has no Sales Order or
	date, so the SO/date-range filters don't apply to it). Ordered oldest-first
	so the report's bottom section is stable as rows are added."""
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	# Defensive: on a site where the app is deployed but not yet migrated, the
	# table won't exist. Return nothing rather than break the FGSRM report (whose
	# execute() calls through here on every run).
	if not frappe.db.table_exists(DOCTYPE):
		return []
	q = _owner_filter()
	if filters.get("item_code"):
		q["item_code"] = filters["item_code"]
	if filters.get("customer"):
		q["customer"] = filters["customer"]

	return frappe.get_all(
		DOCTYPE,
		filters=q,
		fields=[
			"name",
			"item_code",
			"item_name",
			"qty",
			"customer",
			"source_type",
			"source_document",
			"source_item",
			"remarks",
		],
		order_by="creation asc",
	)


def manual_requirement_qty_by_item(filters=None):
	"""{item_code: total manual qty} for the current user under the same
	item/customer scope. Consumed by execute() and _suggested_prodn_by_item() so
	the manual demand nets into Suggested Prodn / Create Prodn Plan."""
	out = {}
	for r in list_manual_requirements(filters):
		out[r["item_code"]] = out.get(r["item_code"], 0.0) + flt(r["qty"])
	return out


@frappe.whitelist()
def get_open_source_lines(source_type, source_document):
	"""Open lines of a Blanket Order / Quotation, for the cherry-pick dialog:
	[{source_item, item_code, item_name, open_qty, rate}, ...].

	  Blanket Order -> Blanket Order Item, open qty = qty - ordered_qty (what
	                   hasn't yet been drawn down into Sales Orders); only lines
	                   with something still open are returned.
	  Quotation     -> Quotation Item, open qty = qty (a Quotation has no
	                   draw-down tracking).
	Read-permission checked on the parent document."""
	if source_type not in SOURCE_TYPES:
		frappe.throw(_("Unsupported source type: {0}").format(source_type))
	if not frappe.has_permission(source_type, "read", doc=source_document):
		frappe.throw(
			_("You are not permitted to read {0} {1}.").format(source_type, source_document),
			frappe.PermissionError,
		)

	if source_type == "Blanket Order":
		rows = frappe.get_all(
			"Blanket Order Item",
			filters={"parent": source_document},
			fields=[
				"name AS source_item",
				"item_code",
				"item_name",
				"qty",
				"ordered_qty",
				"rate",
			],
			order_by="idx asc",
		)
		out = []
		for r in rows:
			open_qty = flt(r.qty) - flt(r.ordered_qty)
			if open_qty > 0.0001:
				out.append(
					{
						"source_item": r.source_item,
						"item_code": r.item_code,
						"item_name": r.item_name,
						"open_qty": open_qty,
						"rate": flt(r.rate),
					}
				)
		return out

	# Quotation
	rows = frappe.get_all(
		"Quotation Item",
		filters={"parent": source_document},
		fields=["name AS source_item", "item_code", "item_name", "qty", "rate"],
		order_by="idx asc",
	)
	return [
		{
			"source_item": r.source_item,
			"item_code": r.item_code,
			"item_name": r.item_name,
			"open_qty": flt(r.qty),
			"rate": flt(r.rate),
		}
		for r in rows
	]


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #

@frappe.whitelist()
def add_manual_requirement(
	item_code,
	qty,
	source_type=None,
	source_document=None,
	source_item=None,
	customer=None,
	remarks=None,
):
	"""Create one manual requirement owned by the current user. Used by both the
	free-form path (item + qty) and the cherry-pick path (one call per ticked
	Blanket Order / Quotation line, carrying source_*)."""
	if not frappe.has_permission(DOCTYPE, "create"):
		frappe.throw(_("You are not permitted to add manual requirements."), frappe.PermissionError)

	qty = flt(qty)
	if not item_code:
		frappe.throw(_("Item is required."))
	if qty <= 0:
		frappe.throw(_("Qty must be greater than zero."))
	if source_type and source_type not in SOURCE_TYPES:
		frappe.throw(_("Unsupported source type: {0}").format(source_type))

	doc = frappe.new_doc(DOCTYPE)
	doc.item_code = item_code
	doc.qty = qty
	doc.source_type = source_type or None
	doc.source_document = source_document or None
	doc.source_item = source_item or None
	doc.customer = customer or None
	doc.remarks = remarks or None
	doc.insert()
	return doc.name


@frappe.whitelist()
def add_source_requirements(source_type, source_document, lines):
	"""Bulk-add cherry-picked lines from one Blanket Order / Quotation in a single
	call. `lines` is a JSON list of {source_item, item_code, qty}; lines with no
	item or a non-positive qty are skipped. Each becomes a manual requirement
	tagged with the source document. Returns {added}."""
	lines = frappe.parse_json(lines) or []
	added = 0
	for ln in lines:
		qty = flt(ln.get("qty"))
		if not ln.get("item_code") or qty <= 0:
			continue
		add_manual_requirement(
			item_code=ln.get("item_code"),
			qty=qty,
			source_type=source_type,
			source_document=source_document,
			source_item=ln.get("source_item"),
		)
		added += 1
	return {"added": added}


@frappe.whitelist()
def update_manual_requirement(name, qty):
	"""Owner-guarded qty edit for a single row."""
	qty = flt(qty)
	if qty <= 0:
		frappe.throw(_("Qty must be greater than zero."))
	doc = _guard_owner(name)
	doc.qty = qty
	doc.save()
	return doc.name


@frappe.whitelist()
def remove_manual_requirements(names):
	"""Delete the given rows (owner-guarded each). `names` is a JSON list."""
	names = frappe.parse_json(names) or []
	removed = 0
	for name in names:
		if not name or not frappe.db.exists(DOCTYPE, name):
			continue
		_guard_owner(name)
		frappe.delete_doc(DOCTYPE, name, ignore_permissions=False)
		removed += 1
	return {"removed": removed}


@frappe.whitelist()
def clear_manual_requirements():
	"""Delete every manual requirement owned by the current user."""
	names = [r.name for r in frappe.get_all(DOCTYPE, filters=_owner_filter(), fields=["name"])]
	for name in names:
		frappe.delete_doc(DOCTYPE, name, ignore_permissions=False)
	return {"removed": len(names)}
