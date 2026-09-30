# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Customer Payment Tracker
========================

Receivables counterpart of Payment Tracker: outstanding (and optionally settled)
Sales Invoices with days-to-due and the actual payments received against each,
so collections can be tracked against terms.

Payment Date column: every allocation posted against the invoice (Payment Entry,
Journal Entry, credit-note adjustment) shown as "DD-MM-YYYY(amount)", multiple
separated by "; ". Derived from GL Entry allocations against the invoice's
customer account, so it reflects real receipts, not just the Payment Schedule.

"Show Transactions with No Due" toggle: off (default) shows only invoices with an
outstanding balance; on also includes fully settled ones (outstanding = 0).
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Sales Invoice"), "fieldname": "name", "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 140},
		{"label": _("Customer PO No"), "fieldname": "po_no", "fieldtype": "Data", "width": 130},
		{"label": _("Customer PO Date"), "fieldname": "po_date", "fieldtype": "Date", "width": 120},
		{"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 120},
		{"label": _("Outstanding"), "fieldname": "outstanding_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 100},
		{"label": _("Payment Date"), "fieldname": "payment_date", "fieldtype": "Data", "width": 220},
		{"label": _("Due In Days"), "fieldname": "due_in_days", "fieldtype": "Int", "width": 100},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 200},
	]


def get_data(filters):
	# Plain SQL (DATEDIFF computed in-DB) - avoids query-builder helpers whose
	# import path varies across Frappe versions.
	conditions = ["si.docstatus = 1", "si.posting_date BETWEEN '2000-03-31' AND CURDATE()"]
	values = {}

	# Toggle: by default only invoices with something outstanding; when
	# "Show Transactions with No Due" is on, include fully settled ones too.
	if not filters.get("show_no_due"):
		conditions.append("si.outstanding_amount > 0")
	if filters.get("customer"):
		conditions.append("si.customer = %(customer)s")
		values["customer"] = filters.get("customer")
	if filters.get("payment_terms_template"):
		conditions.append("c.payment_terms = %(ptt)s")
		values["ptt"] = filters.get("payment_terms_template")

	having = []
	if filters.get("due_in_days") == "Less than 90":
		having.append("ABS(due_in_days) < 90")
	elif filters.get("due_in_days") == "More than 90":
		having.append("ABS(due_in_days) > 90")
	if filters.get("no_of_due_days"):
		having.append("ABS(due_in_days) <= %(nod)s")
		values["nod"] = cint(filters.get("no_of_due_days"))
	having_clause = ("HAVING " + " AND ".join(having)) if having else ""

	data = frappe.db.sql(
		"""
		SELECT si.name, si.posting_date, si.customer, si.po_no, si.grand_total,
			si.po_date, si.outstanding_amount, si.remarks, si.due_date,
			DATEDIFF(si.due_date, CURDATE()) AS due_in_days
		FROM `tabSales Invoice` si
		LEFT JOIN `tabCustomer` c ON c.name = si.customer
		WHERE {conditions}
		{having_clause}
		ORDER BY due_in_days ASC
		""".format(conditions=" AND ".join(conditions), having_clause=having_clause),
		values,
		as_dict=True,
	)

	# Payment Date column: receipts/allocations posted against each invoice.
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
	invoice's customer account (Payment Entry / Journal Entry / credit note), where
	a positive amount reduces the receivable."""
	names = [n for n in names if n]
	if not names:
		return {}
	rows = frappe.db.sql(
		"""
		SELECT against_voucher AS si, posting_date, (credit - debit) AS amt
		FROM `tabGL Entry`
		WHERE against_voucher_type = 'Sales Invoice'
			AND against_voucher IN %(names)s
			AND party_type = 'Customer'
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
		out.setdefault(r.si, []).append((r.posting_date, amt))
	return out


def _fmt_amt(amount):
	return "{0:,.2f}".format(flt(amount))
