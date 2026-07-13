# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
FGSRM Production Plan Excel (3 sheets)
======================================

A Playground-owned workbook the FGSRM "Create Prodn Plan" button downloads after
building a Production Plan - separate from frontec's own MR Hierarchy Excel
(`frontec.doc_event.production_plan.hierarchy_excel`), which is left untouched.

Three sheets:
  1. "FGSRM"               - the FGSRM report view that produced the plan
                             (rebuilt from the same filters).
  2. "FG Requirement"      - consolidated, itemwise finished-goods requirement
                             (itemwise Suggested Prodn).
  3. "RM Component Shortage" - raw-material / component shortage across the plan's
                             nested chain (Short Qty = max(0, qty - in-stock -
                             pending PO); Amount = Short Qty x valuation rate).

Column layouts here are a FIRST PASS - they are meant to be replaced by the
user's supplied per-sheet templates. Each sheet builder is isolated so its
columns/format can be swapped without touching the plumbing.

Built with openpyxl (bundled with Frappe) and streamed via frappe.response.
"""

from io import BytesIO

import frappe
from frappe import _
from frappe.utils import flt

from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
	execute as fgsrm_execute,
	_suggested_prodn_by_item,
)
from playground.playground.report.production_requirement_report.production_requirement_report import (
	get_item_map,
)

# frontec's parent-plan link that chains the nested Production Plans.
PARENT_FIELD = "custom_parent_production_plan"
MAX_LEVELS = 5

# Header styling shared by every sheet.
_HEADER_FILL = "FFE6E6E6"
_HEADER_FONT_BOLD = True


@frappe.whitelist()
def download_fgsrm_mr_excel(name, filters=None):
	"""Stream the 3-sheet workbook for Production Plan `name`. `filters` is the
	FGSRM filter JSON (for the "FGSRM" sheet); optional so the endpoint still
	works when called with just a plan name."""
	if not frappe.has_permission("Production Plan", "read", doc=name):
		frappe.throw(
			_("You are not permitted to read Production Plan {0}.").format(name),
			frappe.PermissionError,
		)

	filters = frappe.parse_json(filters) if filters else {}

	import openpyxl

	wb = openpyxl.Workbook()
	wb.remove(wb.active)  # drop the default empty sheet

	_build_fgsrm_sheet(wb, filters)
	_build_fg_requirement_sheet(wb, filters)
	_build_rm_shortage_sheet(wb, name)

	stream = BytesIO()
	wb.save(stream)

	frappe.response["filename"] = "FGSRM_Prodn_Plan_{0}.xlsx".format(str(name).replace("/", "-"))
	frappe.response["filecontent"] = stream.getvalue()
	frappe.response["type"] = "binary"


# --------------------------------------------------------------------------- #
# Sheet writers (openpyxl helpers)
# --------------------------------------------------------------------------- #

def _write_header(ws, headers):
	"""Write a bold, grey header row at row 1 and freeze it."""
	from openpyxl.styles import Font, PatternFill

	fill = PatternFill("solid", fgColor=_HEADER_FILL)
	font = Font(bold=_HEADER_FONT_BOLD)
	for col, label in enumerate(headers, start=1):
		cell = ws.cell(row=1, column=col, value=label)
		cell.fill = fill
		cell.font = font
	ws.freeze_panes = "A2"


def _autosize(ws, headers):
	"""Rough column widths from the header text (a floor of 12)."""
	from openpyxl.utils import get_column_letter

	for col, label in enumerate(headers, start=1):
		letter = get_column_letter(col)
		ws.column_dimensions[letter].width = max(12, len(str(label)) + 2)


# --------------------------------------------------------------------------- #
# Sheet 1 - FGSRM view (rebuilt from the same filters)
# --------------------------------------------------------------------------- #

def _build_fgsrm_sheet(wb, filters):
	"""Reproduce the FGSRM report (visible columns) for `filters`, so the sheet
	matches what was on screen when the plan was created."""
	ws = wb.create_sheet("FGSRM")

	# Force the per-line view (not the group-by-SO collapse) for a full export.
	view_filters = dict(filters or {})
	view_filters["group_by_so"] = 0
	columns, data = fgsrm_execute(view_filters)

	visible = [c for c in columns if not c.get("hidden")]
	headers = [c.get("label") for c in visible]
	fields = [c.get("fieldname") for c in visible]

	_write_header(ws, headers)
	for row in data:
		ws.append([row.get(f) for f in fields])
	_autosize(ws, headers)


# --------------------------------------------------------------------------- #
# Sheet 2 - Consolidated FG Requirement (itemwise Suggested Prodn)
# --------------------------------------------------------------------------- #

def _build_fg_requirement_sheet(wb, filters):
	"""One row per FG item = its itemwise Suggested Prodn for the filters."""
	ws = wb.create_sheet("FG Requirement")

	# FIRST PASS columns - to be replaced by the user's template.
	headers = ["Item Code", "Item Name", "FG Requirement Qty"]
	_write_header(ws, headers)

	prodn_by_item = _suggested_prodn_by_item(filters or {})
	item_map = get_item_map(sorted(prodn_by_item.keys())) if prodn_by_item else {}
	for item in sorted(prodn_by_item.keys()):
		details = item_map.get(item) or frappe._dict()
		ws.append([item, details.get("item_name"), flt(prodn_by_item[item])])
	_autosize(ws, headers)


# --------------------------------------------------------------------------- #
# Sheet 3 - RM / Component Shortage across the nested plan chain
# --------------------------------------------------------------------------- #

def _build_rm_shortage_sheet(wb, plan_name):
	"""Walk the plan chain, gather raw-material lines, and compute shortage."""
	ws = wb.create_sheet("RM Component Shortage")

	# FIRST PASS columns - to be replaced by the user's template.
	headers = [
		"Production Plan", "Level", "Item Code", "Type", "Warehouse",
		"Required Qty", "In Stock", "On Order (PO)", "Short Qty",
		"Valuation Rate", "Amount",
	]
	_write_header(ws, headers)

	chain = _build_chain(plan_name)
	rows = _collect_mr_rows(chain)
	if not rows:
		_autosize(ws, headers)
		return

	items = sorted({r["item_code"] for r in rows})
	bin_map = _bin_map(rows)
	pending_po = _pending_po_map(items)

	for r in rows:
		item = r["item_code"]
		warehouse = r.get("warehouse")
		required = flt(r.get("quantity"))
		binrow = bin_map.get((item, warehouse)) or frappe._dict()
		in_stock = flt(binrow.get("actual_qty"))
		on_order = flt(pending_po.get(item))
		short = max(0.0, required - in_stock - on_order)
		val_rate = flt(binrow.get("valuation_rate"))
		amount = short * val_rate
		ws.append([
			r["_pp"], r["_level"], item, r.get("material_request_type"), warehouse,
			required, in_stock, on_order, short, val_rate, amount,
		])
	_autosize(ws, headers)


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #

def _build_chain(name):
	"""[root, ..., leaf] for the nested Production Plan chain. Walks up via
	PARENT_FIELD to the root, then down the single-child chain. Capped at
	MAX_LEVELS against circular refs. Degrades to [name] when the frontec parent
	field isn't installed."""
	if not frappe.db.has_column("Production Plan", PARENT_FIELD):
		return [name]

	root = name
	for _i in range(MAX_LEVELS):
		parent = frappe.db.get_value("Production Plan", root, PARENT_FIELD)
		if not parent or parent == root:
			break
		root = parent

	chain = [root]
	seen = {root}
	cur = root
	for _i in range(MAX_LEVELS):
		child = frappe.db.get_value(
			"Production Plan", {PARENT_FIELD: cur, "docstatus": ["<", 2]}, "name"
		)
		if not child or child in seen:
			break
		chain.append(child)
		seen.add(child)
		cur = child
	return chain


def _collect_mr_rows(chain):
	"""Every Material Request Plan Item across the chain, tagged with its plan
	name (_pp) and level label (L1..Ln)."""
	rows = []
	for idx, pp in enumerate(chain, start=1):
		level = "L{0}".format(idx)
		for d in frappe.get_all(
			"Material Request Plan Item",
			filters={"parent": pp, "parenttype": "Production Plan"},
			fields=["item_code", "material_request_type", "quantity", "warehouse"],
		):
			d["_pp"] = pp
			d["_level"] = level
			rows.append(d)
	return rows


def _bin_map(rows):
	"""{(item_code, warehouse): {actual_qty, valuation_rate}} for the rows' items."""
	items = sorted({r["item_code"] for r in rows})
	if not items:
		return {}
	out = {}
	for b in frappe.get_all(
		"Bin",
		filters={"item_code": ["in", items]},
		fields=["item_code", "warehouse", "actual_qty", "valuation_rate"],
	):
		out[(b.item_code, b.warehouse)] = b
	return out


def _pending_po_map(items):
	"""{item_code: outstanding PO qty} = Σ max(qty - received_qty, 0) over
	submitted, non-Closed/Cancelled Purchase Orders."""
	if not items:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT poi.item_code, SUM(GREATEST(poi.qty - poi.received_qty, 0)) AS pending
		FROM `tabPurchase Order Item` poi
		INNER JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE po.docstatus = 1
			AND po.status NOT IN ('Closed', 'Cancelled')
			AND poi.item_code IN %(items)s
		GROUP BY poi.item_code
		""",
		{"items": items},
		as_dict=True,
	)
	return {r.item_code: flt(r.pending) for r in rows}
