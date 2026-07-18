# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Authorization Sheet (PAS)
==================================

A management-level authorization that sits between the Production Plan and the
Purchase Orders: rather than approving individual POs, management approves the
purchase requirement line by line.

Flow:
  1. Create a PAS, attach the MR Hierarchy workbook, click "Populate from Excel"
     -> its "Approved for Purchase" sheet (Item, Qty) fills the item table, each
     row enriched from ERPNext (description, stock, reserved, rate, value,
     vendor, lead time).
  2. Review and tick "Approve" per line (line-wise authorization).
  3. Summary (totals, approved value, cash requirement, ...) and Status recompute
     automatically. Status: Draft -> (submit) Pending Approval -> Approved /
     Partially Approved as lines are ticked (approve is allow_on_submit).

Downstream generation of Material Requests / Purchase Orders from the approved
lines is a deliberate next step, not built here.
"""

from io import BytesIO

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, now_datetime


class PurchaseAuthorizationSheet(Document):
	def validate(self):
		if not self.prepared_by:
			self.prepared_by = frappe.session.user
		if not self.prepared_on:
			self.prepared_on = now_datetime()
		self._recompute()

	def on_submit(self):
		self._recompute()

	def on_update_after_submit(self):
		# "approve" (and the summary fields) are allow_on_submit, so management can
		# tick lines after submission - recompute + persist the rollups/status.
		self._recompute(persist=True)

	def on_cancel(self):
		self.db_set("status", "Rejected")

	# ------------------------------------------------------------------ #
	def _recompute(self, persist=False):
		for d in self.items:
			d.value = flt(d.to_purchase) * flt(d.rate)

		total_items = len(self.items)
		approved_value = sum(flt(d.value) for d in self.items if d.approve)
		vals = {
			"total_items": total_items,
			"approved_value": approved_value,
			"cash_requirement": approved_value,
			"new_purchase_value": sum(flt(d.value) for d in self.items),
			"existing_stock_value": sum(
				min(flt(d.required_qty), flt(d.in_stock)) * flt(d.rate) for d in self.items
			),
			"critical_items": sum(1 for d in self.items if flt(d.in_stock) < flt(d.required_qty)),
			"expected_vendors": len({d.vendor for d in self.items if d.vendor}),
			"status": self._status(total_items),
		}

		if persist:
			for k, v in vals.items():
				self.db_set(k, v, update_modified=False)
			for d in self.items:
				d.db_set("value", d.value, update_modified=False)
		else:
			for k, v in vals.items():
				setattr(self, k, v)

	def _status(self, total_items):
		if self.docstatus == 0:
			return "Draft"
		if self.docstatus == 2:
			return "Rejected"
		approved = sum(1 for d in self.items if d.approve)
		if total_items == 0 or approved == 0:
			return "Pending Approval"
		if approved == total_items:
			return "Approved"
		return "Partially Approved"


# --------------------------------------------------------------------------- #
# Excel upload -> item table
# --------------------------------------------------------------------------- #

APPROVED_SHEET = "Approved for Purchase"


@frappe.whitelist()
def populate_from_excel(docname):
	"""Read the attached workbook's "Approved for Purchase" sheet (Item, Qty) and
	rebuild the item table, enriching each line from ERPNext masters. Returns
	{added, skipped}."""
	doc = frappe.get_doc("Purchase Authorization Sheet", docname)
	doc.check_permission("write")
	if not doc.upload_excel:
		frappe.throw(_("Attach an Excel file in 'Upload Excel' first."))

	rows = _read_approved_sheet(doc.upload_excel)
	if not rows:
		frappe.throw(_("No data rows found in the '{0}' sheet.").format(APPROVED_SHEET))

	doc.set("items", [])
	skipped = []
	for item_code, qty in rows:
		if not item_code or flt(qty) <= 0:
			continue
		if not frappe.db.exists("Item", item_code):
			skipped.append(item_code)
			continue
		doc.append("items", _build_item_row(item_code, flt(qty)))

	doc.save()
	return {"added": len(doc.items), "skipped": skipped}


def _read_approved_sheet(file_url):
	"""[(item, qty), ...] from column A/B of the "Approved for Purchase" sheet,
	skipping the header row."""
	import openpyxl
	from frappe.utils.file_manager import get_file

	_name, content = get_file(file_url)
	wb = openpyxl.load_workbook(BytesIO(content), data_only=True, read_only=True)

	target = None
	for ws in wb.worksheets:
		if (ws.title or "").strip().lower() == APPROVED_SHEET.lower():
			target = ws
			break
	if target is None:
		frappe.throw(_("The uploaded file has no '{0}' sheet.").format(APPROVED_SHEET))

	rows = []
	for i, row in enumerate(target.iter_rows(values_only=True)):
		if i == 0 or not row:
			continue  # header / blank
		item = str(row[0]).strip() if row[0] is not None else ""
		qty = row[1] if len(row) > 1 else None
		if not item or item.lower() == "total":
			continue
		rows.append((item, qty))
	return rows


def _build_item_row(item_code, qty):
	it = frappe.get_cached_value(
		"Item", item_code, ["item_name", "stock_uom", "valuation_rate", "lead_time_days"], as_dict=True
	) or frappe._dict()
	actual, reserved = _stock(item_code)
	rate = flt(it.valuation_rate)
	return {
		"item_code": item_code,
		"description": it.item_name,
		"required_qty": qty,
		"in_stock": actual,
		"reserved": reserved,
		"to_purchase": qty,
		"uom": it.stock_uom,
		"rate": rate,
		"value": qty * rate,
		"lead_time": cint(it.lead_time_days),
		"vendor": _default_supplier(item_code),
		"approve": 0,
	}


def _stock(item_code):
	"""(actual_qty, reserved_qty) summed across all warehouses for the item."""
	row = frappe.db.sql(
		"SELECT SUM(actual_qty), SUM(reserved_qty) FROM `tabBin` WHERE item_code = %s",
		item_code,
	)
	if row and row[0] and row[0][0] is not None:
		return flt(row[0][0]), flt(row[0][1])
	return 0.0, 0.0


def _default_supplier(item_code):
	return frappe.db.get_value("Item Default", {"parent": item_code}, "default_supplier")
