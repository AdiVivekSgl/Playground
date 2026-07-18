# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Payment Tracker
===============

Outstanding (and optionally settled) Purchase Invoices with days-to-due and the
actual payments made against each, so payables can be tracked against terms.

Payment Date column: every allocation posted against the invoice (Payment Entry,
Journal Entry, debit-note adjustment) shown as "DD-MM-YYYY(amount)", multiple
separated by "; ". Derived from GL Entry allocations against the invoice's
supplier account, so it reflects real settlements, not just the Payment Schedule.

"Show Transactions with No Due" toggle: off (default) shows only invoices with an
outstanding balance; on also includes fully settled ones (outstanding = 0).
"""

import frappe
from frappe import _
from frappe.utils import flt, formatdate
from frappe.query_builder import Field
from frappe.query_builder.custom import CustomFunction
from frappe.query_builder.functions import Abs, CurDate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Purchase Invoice"), "fieldname": "name", "fieldtype": "Link", "options": "Purchase Invoice", "width": 150},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 140},
		{"label": _("Supplier Invoice No"), "fieldname": "bill_no", "fieldtype": "Data", "width": 130},
		{"label": _("Supplier Invoice Date"), "fieldname": "bill_date", "fieldtype": "Date", "width": 120},
		{"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 120},
		{"label": _("Outstanding"), "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 100},
		{"label": _("Payment Date"), "fieldname": "payment_date", "fieldtype": "Data", "width": 220},
		{"label": _("Due In Days"), "fieldname": "due_in_days", "fieldtype": "Int", "width": 100},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 200},
	]


def get_data(filters):
	PI = frappe.qb.DocType("Purchase Invoice")
	SUPPLIER = frappe.qb.DocType("Supplier")
	datediff = CustomFunction("DATEDIFF", ["cur_date", "due_date"])

	query = (
		frappe.qb.from_(PI)
		.left_join(SUPPLIER).on(PI.supplier == SUPPLIER.name)
		.select(
			PI.name, PI.posting_date, PI.supplier, PI.bill_no, PI.grand_total,
			PI.bill_date, PI.outstanding_amount, PI.remarks, PI.due_date,
			datediff(PI.due_date, CurDate()).as_("due_in_days"),
		)
		.where(
			(PI.docstatus == 1)
			& (PI.posting_date.between("2000-03-31", CurDate()))
		)
		.orderby(Field("due_in_days"), order=frappe.qb.asc)
	)

	# Toggle: by default only invoices with something outstanding; when
	# "Show Transactions with No Due" is on, include fully settled ones too.
	if not filters.get("show_no_due"):
		query = query.where(PI.outstanding_amount > 0)

	if filters.get("supplier"):
		query = query.where(PI.supplier == filters.get("supplier"))
	if filters.get("payment_terms_template"):
		query = query.where(SUPPLIER.payment_terms == filters.get("payment_terms_template"))
	if filters.get("due_in_days") == "Less than 90":
		query = query.having(Abs(Field("due_in_days")) < 90)
	if filters.get("due_in_days") == "More than 90":
		query = query.having(Abs(Field("due_in_days")) > 90)
	if filters.get("no_of_due_days"):
		query = query.having(Abs(Field("due_in_days")) <= filters.get("no_of_due_days"))

	data = query.run(as_dict=True)

	# Payment Date column: payments/allocations posted against each invoice.
	pay_map = _payment_map([r["name"] for r in data])
	for row in data:
		pays = pay_map.get(row["name"]) or []
		row["payment_date"] = "; ".join(
			"{0}({1})".format(formatdate(d), _fmt_amt(a)) for d, a in pays
		)

	sum_grand_total = sum(flt(r["grand_total"]) for r in data)
	sum_outstanding = sum(flt(r["outstanding_amount"]) for r in data)
	data.append({
		"name": "Total",
		"grand_total": sum_grand_total,
		"outstanding_amount": sum_outstanding,
	})
	return data


def _payment_map(names):
	"""{invoice: [(posting_date, amount), ...]} - allocations posted against each
	invoice's supplier account (Payment Entry / Journal Entry / debit note), where
	a positive amount reduces the payable."""
	names = [n for n in names if n]
	if not names:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT against_voucher AS pi, posting_date, (debit - credit) AS amt
		FROM `tabGL Entry`
		WHERE against_voucher_type = 'Purchase Invoice'
			AND against_voucher IN %(names)s
			AND party_type = 'Supplier'
			AND is_cancelled = 0
			AND voucher_no <> against_voucher
		ORDER BY posting_date, creation
		""",
		{"names": names},
		as_dict=True,
	)
	out = {}
	for r in rows:
		amt = flt(r.amt)
		if abs(amt) < 0.005:
			continue
		out.setdefault(r.pi, []).append((r.posting_date, amt))
	return out


def _fmt_amt(amount):
	return "{0:,.2f}".format(flt(amount))
