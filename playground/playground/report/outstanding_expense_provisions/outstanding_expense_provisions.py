# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Outstanding Expense Provisions
==============================

Expense Provisions by month and expense account, showing which are still Open
(un-reversed) versus Reversed, and - for the Open ones - how long they have been
sitting (age). The year-end view: filter to Open only to find provisions that were
booked but never actioned by an actual document.

A provision reverses in full the moment an actual document is linked to it, so
there is no partial / outstanding balance - a provision is simply Open (full
amount still accrued) or Reversed (fully unwound against `reversed_against`).
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	conds = {"docstatus": 1, "company": filters.company}
	if filters.get("expense_account"):
		conds["expense_account"] = filters.expense_account
	if filters.get("from_date") and filters.get("to_date"):
		conds["posting_date"] = ["between", [filters.from_date, filters.to_date]]
	if filters.get("open_only"):
		conds["status"] = "Open"
	elif filters.get("status"):
		conds["status"] = filters.status

	provisions = frappe.get_all(
		"Expense Provision",
		filters=conds,
		fields=[
			"name", "posting_date", "expense_account", "provision_account",
			"party_type", "party", "cost_center", "provision_amount", "status",
			"reversed_on", "reversed_against_type", "reversed_against",
		],
		order_by="posting_date, expense_account, name",
	)

	today = getdate(nowdate())
	data = []
	for p in provisions:
		is_open = p.status == "Open"
		data.append({
			"provision": p.name,
			"posting_date": p.posting_date,
			"month": getdate(p.posting_date).strftime("%b %Y") if p.posting_date else None,
			"expense_account": p.expense_account,
			"provision_account": p.provision_account,
			"party": p.party,
			"cost_center": p.cost_center,
			"provision_amount": flt(p.provision_amount),
			"open_amount": flt(p.provision_amount) if is_open else 0.0,
			"status": p.status,
			"reversed_on": p.reversed_on,
			"reversed_against": p.reversed_against,
			# Age: for Open provisions, how long the accrual has been un-actioned.
			"age": date_diff(today, p.posting_date) if (is_open and p.posting_date) else None,
		})

	return get_columns(), data


def get_columns():
	return [
		{"label": _("Provision"), "fieldname": "provision", "fieldtype": "Link", "options": "Expense Provision", "width": 150},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Month"), "fieldname": "month", "fieldtype": "Data", "width": 90},
		{"label": _("Expense Account"), "fieldname": "expense_account", "fieldtype": "Link", "options": "Account", "width": 200},
		{"label": _("Provision Account"), "fieldname": "provision_account", "fieldtype": "Link", "options": "Account", "width": 200},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Data", "width": 130},
		{"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 130},
		{"label": _("Provision Amount"), "fieldname": "provision_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Open Amount"), "fieldname": "open_amount", "fieldtype": "Currency", "width": 120},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Reversed On"), "fieldname": "reversed_on", "fieldtype": "Date", "width": 100},
		{"label": _("Reversed Against"), "fieldname": "reversed_against", "fieldtype": "Data", "width": 160},
		{"label": _("Age (Days)"), "fieldname": "age", "fieldtype": "Int", "width": 90},
	]
