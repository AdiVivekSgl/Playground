# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Price Adjustment GRNI Reclassification - reconciliation
=======================================================

Lists Debit Notes flagged as price adjustments and their current GRNI (Stock
Received But Not Billed) residual, so you can confirm each one has been cleared -
either automatically at posting (get_gl_entries reclassification) or by the
one-time cleanup patch's Journal Entry (marker "PARECLASS:<debit note>").

A "Residual" status flags a debit note whose GRNI hasn't netted to zero yet
(typically a historical one that hasn't been flagged + patched).
"""

import frappe
from frappe import _
from frappe.utils import flt

MARKER = "PARECLASS"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	grni = frappe.get_cached_value("Company", filters.company, "stock_received_but_not_billed")

	conds = {"docstatus": 1, "is_return": 1, "custom_is_price_adjustment_debit_note": 1, "company": filters.company}
	if filters.get("supplier"):
		conds["supplier"] = filters.supplier
	if filters.get("from_date") and filters.get("to_date"):
		conds["posting_date"] = ["between", [filters.from_date, filters.to_date]]

	notes = frappe.get_all(
		"Purchase Invoice", filters=conds,
		fields=["name", "posting_date", "supplier", "supplier_name", "return_against"],
		order_by="posting_date, name",
	)

	data = []
	for n in notes:
		residual = _grni_residual(n.name, grni) if grni else 0.0
		je = _reclass_je(n.name)
		data.append({
			"debit_note": n.name,
			"posting_date": n.posting_date,
			"supplier": n.supplier,
			"supplier_name": n.supplier_name,
			"original_pi": n.return_against,
			"grni_residual": residual,
			"reclass_je": je,
			"status": _("Residual") if abs(residual) > 0.01 else _("Cleared"),
		})

	return get_columns(), data, None, None, _summary(data)


def get_columns():
	return [
		{"label": _("Debit Note"), "fieldname": "debit_note", "fieldtype": "Link", "options": "Purchase Invoice", "width": 160},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 130},
		{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 160},
		{"label": _("Original PI"), "fieldname": "original_pi", "fieldtype": "Link", "options": "Purchase Invoice", "width": 160},
		{"label": _("GRNI Residual"), "fieldname": "grni_residual", "fieldtype": "Currency", "width": 130},
		{"label": _("Reclass JE"), "fieldname": "reclass_je", "fieldtype": "Link", "options": "Journal Entry", "width": 160},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
	]


def _grni_residual(dn, grni):
	row = frappe.db.sql(
		"""
		SELECT SUM(credit - debit) AS net
		FROM `tabGL Entry`
		WHERE voucher_type = 'Purchase Invoice' AND voucher_no = %(dn)s
			AND account = %(grni)s AND is_cancelled = 0
		""",
		{"dn": dn, "grni": grni},
	)
	return flt(row[0][0]) if row and row[0][0] is not None else 0.0


def _reclass_je(dn):
	je = frappe.get_all(
		"Journal Entry",
		filters={"docstatus": 1, "user_remark": ["like", "%{0}:{1}%".format(MARKER, dn)]},
		pluck="name", limit=1,
	)
	return je[0] if je else None


def _summary(data):
	residual = sum(flt(r["grni_residual"]) for r in data)
	unresolved = sum(1 for r in data if r["status"] == _("Residual"))
	return [
		{"label": _("Flagged Debit Notes"), "value": len(data), "datatype": "Int", "indicator": "Blue"},
		{"label": _("With GRNI Residual"), "value": unresolved, "datatype": "Int", "indicator": "Red" if unresolved else "Green"},
		{"label": _("Total GRNI Residual"), "value": residual, "datatype": "Currency", "indicator": "Red" if abs(residual) > 0.01 else "Green"},
	]
