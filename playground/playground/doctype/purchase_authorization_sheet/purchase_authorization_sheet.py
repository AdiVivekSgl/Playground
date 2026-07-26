# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Authorization Sheet (PAS)
==================================

A management-level authorization that sits between the Production Plan and the
Purchase Orders: rather than approving individual POs, management approves the
purchase requirement line by line.

Flow:
  1. Create a PAS, attach the workbook, click "Populate from Excel" -> its
     "Approved for Purchase" sheet (Item, Qty, and optionally Vendor) fills the
     item table, each row enriched from ERPNext (description, stock, reserved,
     rate, value, lead time). Vendor is taken from the sheet when it names a real
     Supplier, else the item's default supplier; either way it stays editable
     (Select any Supplier) in the form afterwards.
  2. Review and tick "Approve" per line (line-wise authorization).
  3. Summary (totals, approved value, cash requirement, ...) and Status recompute
     automatically. Status: Draft -> (submit) Pending Approval -> Approved /
     Partially Approved as lines are ticked (approve is allow_on_submit).

Downstream generation of Purchase Orders from the approved lines is done by
`create_purchase_orders`: approved lines are grouped by vendor and one draft PO
is raised per vendor. Each contributing line records its PO in `purchase_order`,
so the action is idempotent - re-running only picks up newly approved lines.
Approved lines with no vendor cannot go on a PO and are reported back as skipped.
"""

from io import BytesIO

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate


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
	"""Read the attached workbook's "Approved for Purchase" sheet (Item, Qty, and
	optionally Vendor) and rebuild the item table, enriching each line from ERPNext
	masters. Returns {added, skipped}."""
	doc = frappe.get_doc("Purchase Authorization Sheet", docname)
	doc.check_permission("write")
	if not doc.upload_excel:
		frappe.throw(_("Attach an Excel file in 'Upload Excel' first."))

	rows = _read_approved_sheet(doc.upload_excel)
	if not rows:
		frappe.throw(_("No data rows found in the '{0}' sheet.").format(APPROVED_SHEET))

	doc.set("items", [])
	skipped = []
	for r in rows:
		item_code = r["item_code"]
		if not item_code or flt(r["qty"]) <= 0:
			continue
		if not frappe.db.exists("Item", item_code):
			skipped.append(item_code)
			continue
		doc.append("items", _build_item_row(item_code, flt(r["qty"]), r.get("vendor")))

	doc.save()
	return {"added": len(doc.items), "skipped": skipped}


def _read_approved_sheet(file_url):
	"""[{"item_code", "qty", "vendor"}, ...] from the "Approved for Purchase" sheet.

	Columns are located by header label (case-insensitive) so the sheet can carry
	extra columns in any order: "Item"/"Item Code" -> item, "Qty"/"Quantity" ->
	qty, "Vendor"/"Supplier" -> vendor. Falls back to the legacy positional layout
	(column A = item, column B = qty, no vendor) when those headers aren't found -
	so an older sheet still imports. The header row and any "Total" row are skipped."""
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
	item_col, qty_col, vendor_col = 0, 1, None  # legacy positional defaults
	for i, row in enumerate(target.iter_rows(values_only=True)):
		if i == 0:
			# Locate columns by header label; keep positional defaults if absent.
			header = {
				str(v).strip().lower(): idx
				for idx, v in enumerate(row or [])
				if v is not None and str(v).strip()
			}
			item_col = header.get("item", header.get("item code", 0))
			qty_col = header.get("qty", header.get("quantity", 1))
			vendor_col = header.get("vendor", header.get("supplier"))
			continue
		if not row:
			continue
		item = str(row[item_col]).strip() if item_col < len(row) and row[item_col] is not None else ""
		qty = row[qty_col] if qty_col < len(row) else None
		vendor = (
			str(row[vendor_col]).strip()
			if vendor_col is not None and vendor_col < len(row) and row[vendor_col] is not None
			else None
		)
		if not item or item.lower() == "total":
			continue
		rows.append({"item_code": item, "qty": qty, "vendor": vendor or None})
	return rows


def _build_item_row(item_code, qty, vendor=None):
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
		"vendor": _resolve_vendor(vendor, item_code),
		"approve": 0,
	}


def _resolve_vendor(vendor, item_code):
	"""Prefer the vendor named in the upload when it's a real Supplier; otherwise
	fall back to the item's default supplier. Either way it stays editable in the
	form afterwards (the Vendor field is a plain Supplier link)."""
	if vendor and frappe.db.exists("Supplier", vendor):
		return vendor
	return _default_supplier(item_code)


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


# --------------------------------------------------------------------------- #
# Approved lines -> draft Purchase Orders (grouped by vendor)
# --------------------------------------------------------------------------- #


@frappe.whitelist()
def create_purchase_orders(docname):
	"""Raise one draft Purchase Order per vendor from the approved, not-yet-ordered
	lines. Each contributing line records the PO in `purchase_order` (so a re-run
	only picks up newly approved lines). Returns
	{created: [po...], skipped: [{item, reason}...]}."""
	if not frappe.has_permission("Purchase Order", "create"):
		frappe.throw(_("You are not permitted to create Purchase Orders."), frappe.PermissionError)

	doc = frappe.get_doc("Purchase Authorization Sheet", docname)
	doc.check_permission("read")
	if doc.docstatus != 1:
		frappe.throw(_("Submit the sheet and approve lines before creating Purchase Orders."))

	# Group eligible lines by vendor; collect the reasons for anything left out.
	by_vendor = {}
	skipped = []
	for d in doc.items:
		if not d.approve:
			continue
		if d.purchase_order:
			skipped.append({"item": d.item_code, "reason": _("Already on {0}").format(d.purchase_order)})
			continue
		if flt(d.to_purchase) <= 0:
			skipped.append({"item": d.item_code, "reason": _("Nothing to purchase")})
			continue
		if not d.vendor:
			skipped.append({"item": d.item_code, "reason": _("No vendor")})
			continue
		by_vendor.setdefault(d.vendor, []).append(d)

	if not by_vendor:
		return {"created": [], "skipped": skipped}

	created = []
	for vendor, lines in by_vendor.items():
		po = frappe.new_doc("Purchase Order")
		po.supplier = vendor
		po.company = doc.company
		po.transaction_date = nowdate()
		po.schedule_date = _schedule_date(lines[0])
		for d in lines:
			po.append("items", {
				"item_code": d.item_code,
				"qty": flt(d.to_purchase),
				"uom": d.uom,
				"rate": flt(d.rate),
				"schedule_date": _schedule_date(d),
			})
		po.insert()  # draft; reviewer submits it

		for d in lines:
			d.db_set("purchase_order", po.name, update_modified=False)
		created.append(po.name)

	return {"created": created, "skipped": skipped}


def _schedule_date(row):
	"""Required-by date for a PO line: the line's Required By, else today + lead time."""
	if row.required_by:
		return getdate(row.required_by)
	return add_days(nowdate(), cint(row.lead_time))
