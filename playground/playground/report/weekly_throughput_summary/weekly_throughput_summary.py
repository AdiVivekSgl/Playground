# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Weekly Throughput Summary
===========================

Independent of the Weekly Planning Snapshot mechanism entirely - a plain
date-range query, always answerable for any past week:

  Section A: Sales Orders booked in range   (Sales Order.transaction_date)
  Section B: SO lines dispatched in range   (Delivery Note.posting_date,
             joined back to the originating Sales Order Item via
             Delivery Note Item.against_sales_order / so_detail)

The "Section" filter switches which table is shown; report_summary cards
report both totals regardless of which section is selected (mirrors the
summary-card pattern in production_requirement_report.py).
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, nowdate


def execute(filters=None):
	filters = filters or {}
	section = filters.get("section") or "Sales Orders Booked"
	from_date = filters.get("from_date") or add_days(nowdate(), -7)
	to_date = filters.get("to_date") or nowdate()

	booked = _get_booked_sos(from_date, to_date, filters)
	dispatched = _get_dispatched_lines(from_date, to_date, filters)

	report_summary = [
		{"label": _("Sales Orders Booked"), "value": len(booked), "datatype": "Int"},
		{
			"label": _("Booked Value"),
			"value": sum(flt(r.grand_total) for r in booked),
			"datatype": "Currency",
		},
		{"label": _("SO Lines Dispatched"), "value": len(dispatched), "datatype": "Int"},
		{
			"label": _("Dispatched Qty"),
			"value": sum(flt(r.qty) for r in dispatched),
			"datatype": "Float",
		},
	]

	if section == "SO Lines Dispatched":
		return _dispatched_columns(), dispatched, None, None, report_summary
	return _booked_columns(), booked, None, None, report_summary


def _get_booked_sos(from_date, to_date, filters):
	conditions = ""
	values = {"from_date": from_date, "to_date": to_date}

	if filters.get("customer"):
		conditions += " AND so.customer = %(customer)s"
		values["customer"] = filters.get("customer")

	return frappe.db.sql(
		"""
		SELECT
			so.name AS sales_order,
			so.transaction_date,
			so.customer,
			so.status,
			so.base_grand_total AS grand_total
		FROM `tabSales Order` so
		WHERE so.docstatus = 1
			AND so.transaction_date BETWEEN %(from_date)s AND %(to_date)s
			{conditions}
		ORDER BY so.transaction_date ASC
		""".format(conditions=conditions),
		values,
		as_dict=True,
	)


def _get_dispatched_lines(from_date, to_date, filters):
	conditions = ""
	values = {"from_date": from_date, "to_date": to_date}

	if filters.get("customer"):
		conditions += " AND dn.customer = %(customer)s"
		values["customer"] = filters.get("customer")

	if filters.get("item_code"):
		conditions += " AND dni.item_code = %(item_code)s"
		values["item_code"] = filters.get("item_code")

	return frappe.db.sql(
		"""
		SELECT
			dn.name AS delivery_note,
			dn.posting_date,
			dn.customer,
			dni.item_code,
			dni.qty,
			dni.against_sales_order AS sales_order,
			dni.so_detail AS sales_order_item
		FROM `tabDelivery Note Item` dni
		INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
		WHERE dn.docstatus = 1
			AND dn.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{conditions}
		ORDER BY dn.posting_date ASC
		""".format(conditions=conditions),
		values,
		as_dict=True,
	)


def _booked_columns():
	return [
		{"label": _("Sales Order"), "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 130},
		{"label": _("Date"), "fieldname": "transaction_date", "fieldtype": "Date", "width": 100},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
		{"label": _("Grand Total"), "fieldname": "grand_total", "fieldtype": "Currency", "width": 130},
	]


def _dispatched_columns():
	return [
		{"label": _("Delivery Note"), "fieldname": "delivery_note", "fieldtype": "Link", "options": "Delivery Note", "width": 140},
		{"label": _("Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 160},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 90},
		{"label": _("Sales Order"), "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 120},
		{"label": _("SO Item"), "fieldname": "sales_order_item", "fieldtype": "Data", "hidden": 1, "width": 100},
	]
