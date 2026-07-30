# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Outstanding Expense Provisions
==============================

Provisions by month and expense account with provisioned amount, amount utilized,
balance outstanding, actual expense booked, variance and age. Filter to
outstanding-only to surface accruals that were never settled - the year-end view
that flags provisions to write back or chase.

  - Provisioned  : Expense Provision.provision_amount
  - Utilized      : sum of settlement rows (provision consumed by actuals)
  - Outstanding   : Provisioned - Utilized
  - Actual        : Utilized + over-provision variance = total actual expense
                    matched against the provision so far
  - Variance      : Actual - Provisioned (positive = actuals exceeded the estimate)
  - Age (days)    : today - posting_date, for ageing unsettled provisions
"""

import frappe
from frappe import _
from frappe.utils import date_diff, flt, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	rows = _fetch(filters)
	return get_columns(), rows


def _fetch(filters):
	conds = {"docstatus": 1, "company": filters.company}
	if filters.get("expense_account"):
		conds["expense_account"] = filters.expense_account
	if filters.get("from_date") and filters.get("to_date"):
		conds["posting_date"] = ["between", [filters.from_date, filters.to_date]]
	if filters.get("status"):
		conds["status"] = filters.status
	if filters.get("outstanding_only"):
		conds["status"] = ["in", ["Open", "Partially Settled"]]

	provisions = frappe.get_all(
		"Expense Provision",
		filters=conds,
		fields=[
			"name", "posting_date", "expense_account", "provision_account",
			"party_type", "party", "cost_center",
			"provision_amount", "utilized_amount", "outstanding_amount", "status",
		],
		order_by="posting_date, expense_account, name",
	)

	# Actual = utilized + total over-provision variance recorded on the settlements.
	variance_by_provision = _settlement_variance(p.name for p in provisions)

	today = getdate(nowdate())
	data = []
	for p in provisions:
		over_variance = flt(variance_by_provision.get(p.name))
		actual = flt(p.utilized_amount) + over_variance
		data.append({
			"provision": p.name,
			"posting_date": p.posting_date,
			"month": getdate(p.posting_date).strftime("%b %Y") if p.posting_date else None,
			"expense_account": p.expense_account,
			"provision_account": p.provision_account,
			"party": p.party,
			"cost_center": p.cost_center,
			"provisioned": flt(p.provision_amount),
			"utilized": flt(p.utilized_amount),
			"outstanding": flt(p.outstanding_amount),
			"actual": actual,
			"variance": actual - flt(p.provision_amount),
			"age": date_diff(today, p.posting_date) if p.posting_date else None,
			"status": p.status,
		})
	return data


def _settlement_variance(provision_names):
	"""Sum of the (over-provision) variance recorded on settlement rows, per parent."""
	names = list(provision_names)
	if not names:
		return {}
	rows = frappe.get_all(
		"Expense Provision Settlement",
		filters={"parenttype": "Expense Provision", "parent": ["in", names]},
		fields=["parent", "variance"],
	)
	out = {}
	for r in rows:
		out[r.parent] = out.get(r.parent, 0.0) + flt(r.variance)
	return out


def get_columns():
	return [
		{"label": _("Provision"), "fieldname": "provision", "fieldtype": "Link", "options": "Expense Provision", "width": 150},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Month"), "fieldname": "month", "fieldtype": "Data", "width": 90},
		{"label": _("Expense Account"), "fieldname": "expense_account", "fieldtype": "Link", "options": "Account", "width": 200},
		{"label": _("Provision Account"), "fieldname": "provision_account", "fieldtype": "Link", "options": "Account", "width": 200},
		{"label": _("Party"), "fieldname": "party", "fieldtype": "Data", "width": 130},
		{"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 130},
		{"label": _("Provisioned"), "fieldname": "provisioned", "fieldtype": "Currency", "width": 120},
		{"label": _("Utilized"), "fieldname": "utilized", "fieldtype": "Currency", "width": 120},
		{"label": _("Outstanding"), "fieldname": "outstanding", "fieldtype": "Currency", "width": 120},
		{"label": _("Actual"), "fieldname": "actual", "fieldtype": "Currency", "width": 120},
		{"label": _("Variance"), "fieldname": "variance", "fieldtype": "Currency", "width": 110},
		{"label": _("Age (Days)"), "fieldname": "age", "fieldtype": "Int", "width": 90},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
	]
