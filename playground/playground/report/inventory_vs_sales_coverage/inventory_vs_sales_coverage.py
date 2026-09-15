# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Inventory vs Sales Coverage
===========================

Maps current inventory against recent sales to show, per item, how long the
stock on hand will last at the recent selling rate ("days of cover") and to
flag stockouts, reorder candidates, excess stock and non-moving items.

Inputs
------
* **On-hand stock** - ``tabBin.actual_qty`` summed across the company's
  warehouses (optional Warehouse filter), with ``stock_value`` for inventory
  value. Current snapshot, the same Bin basis used elsewhere in this app.
* **Sales** - submitted ``Sales Invoice`` lines over the trailing window
  (``qty`` / ``base_net_amount``, company currency), the same billed-sales
  basis as the Item-wise Sales vs Production report. Returns (credit notes)
  net down automatically.

Window
------
``as_on_date`` (default today) ends the window; ``days`` (default 90) is its
length, so sales are counted over ``[as_on_date - days, as_on_date]`` and the
average daily rate divides by ``days``.

Derived metrics (per item)
--------------------------
* **Avg Daily Qty** = Sales Qty / days
* **Days of Cover** = On-hand Qty / Avg Daily Qty (blank when there were no
  sales in the window - a non-moving item, not "infinite cover")
* **Status**:
    - ``Stockout``    - sold in the window but nothing on hand now
    - ``Reorder``     - days of cover below ``reorder_days`` (default 30)
    - ``Excess``      - days of cover above ``excess_days`` (default 180)
    - ``No Recent Sales`` - stock on hand but no sales in the window
    - ``OK``          - otherwise

Rows cover every item with stock on hand OR sales in the window, most urgent
first (stockouts, then rising days of cover; non-moving items last).
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})

	days = cint(filters.get("days")) or 90
	if days <= 0:
		days = 90
	reorder_days = cint(filters.get("reorder_days")) or 30
	excess_days = cint(filters.get("excess_days")) or 180

	to_date = getdate(filters.get("as_on_date") or nowdate())
	from_date = add_days(to_date, -days)

	stock = get_stock_map(filters)
	sales = get_sales_map(filters, from_date, to_date)

	rows, summary = build_rows(filters, stock, sales, days, reorder_days, excess_days)
	return get_columns(filters, days), rows, None, None, summary


# --------------------------------------------------------------------------- #
# Scope helpers
# --------------------------------------------------------------------------- #

def _bin_scope(filters, params):
	join = ""
	conditions = ""
	if filters.get("company"):
		join += " INNER JOIN `tabWarehouse` w ON w.name = b.warehouse"
		conditions += " AND w.company = %(company)s"
		params["company"] = filters.get("company")
	if filters.get("warehouse"):
		conditions += " AND b.warehouse = %(warehouse)s"
		params["warehouse"] = filters.get("warehouse")
	if filters.get("item_group"):
		join += " INNER JOIN `tabItem` it ON it.name = b.item_code"
		conditions += " AND it.item_group = %(item_group)s"
		params["item_group"] = filters.get("item_group")
	return join, conditions


def _sales_scope(filters, params):
	conditions = ""
	if filters.get("company"):
		conditions += " AND si.company = %(company)s"
		params["company"] = filters.get("company")
	if filters.get("warehouse"):
		conditions += " AND sii.warehouse = %(warehouse)s"
		params["warehouse"] = filters.get("warehouse")
	if filters.get("item_group"):
		conditions += " AND sii.item_group = %(item_group)s"
		params["item_group"] = filters.get("item_group")
	return conditions


# --------------------------------------------------------------------------- #
# 1. On-hand stock (current Bin snapshot)
# --------------------------------------------------------------------------- #

def get_stock_map(filters):
	params = {}
	join, conditions = _bin_scope(filters, params)

	data = frappe.db.sql(
		"""
		SELECT
			b.item_code           AS item_code,
			SUM(b.actual_qty)     AS on_hand_qty,
			SUM(b.stock_value)    AS stock_value
		FROM `tabBin` b
		{join}
		WHERE 1 = 1
			{conditions}
		GROUP BY b.item_code
		HAVING SUM(b.actual_qty) <> 0 OR SUM(b.stock_value) <> 0
		""".format(join=join, conditions=conditions),
		params,
		as_dict=True,
	)
	return {d.item_code: d for d in data}


# --------------------------------------------------------------------------- #
# 2. Sales over the window (billed, company currency)
# --------------------------------------------------------------------------- #

def get_sales_map(filters, from_date, to_date):
	params = {"from_date": from_date, "to_date": to_date}
	conditions = _sales_scope(filters, params)

	data = frappe.db.sql(
		"""
		SELECT
			sii.item_code               AS item_code,
			SUM(sii.qty)                AS sales_qty,
			SUM(sii.base_net_amount)    AS sales_value
		FROM `tabSales Invoice` si
		INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE si.docstatus = 1
			AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{conditions}
		GROUP BY sii.item_code
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)
	return {d.item_code: d for d in data}


# --------------------------------------------------------------------------- #
# 3. Merge + derived metrics + status
# --------------------------------------------------------------------------- #

def build_rows(filters, stock, sales, days, reorder_days, excess_days):
	item_codes = set(stock) | set(sales)

	meta = {}
	if item_codes:
		for it in frappe.get_all(
			"Item",
			filters={"name": ["in", list(item_codes)]},
			fields=["name", "item_name", "item_group", "stock_uom"],
		):
			meta[it.name] = it

	rows = []
	summary_acc = {
		"stock_value": 0.0, "sales_value": 0.0,
		"stockout": 0, "reorder": 0, "excess": 0, "no_sales": 0,
	}

	for item_code in item_codes:
		s = stock.get(item_code)
		sl = sales.get(item_code)

		on_hand_qty = flt(s.on_hand_qty) if s else 0.0
		stock_value = flt(s.stock_value) if s else 0.0
		sales_qty = flt(sl.sales_qty) if sl else 0.0
		sales_value = flt(sl.sales_value) if sl else 0.0

		avg_daily_qty = sales_qty / days if sales_qty else 0.0
		days_of_cover = (max(on_hand_qty, 0.0) / avg_daily_qty) if avg_daily_qty > 0 else None

		if sales_qty <= 0:
			status = "No Recent Sales"
			summary_acc["no_sales"] += 1
		elif on_hand_qty <= 0:
			status = "Stockout"
			summary_acc["stockout"] += 1
		elif days_of_cover < reorder_days:
			status = "Reorder"
			summary_acc["reorder"] += 1
		elif days_of_cover > excess_days:
			status = "Excess"
			summary_acc["excess"] += 1
		else:
			status = "OK"

		summary_acc["stock_value"] += stock_value
		summary_acc["sales_value"] += sales_value

		m = meta.get(item_code)
		rows.append({
			"item_code": item_code,
			"item_name": m.item_name if m else None,
			"item_group": m.item_group if m else None,
			"stock_uom": m.stock_uom if m else None,
			"on_hand_qty": on_hand_qty,
			"stock_value": stock_value,
			"sales_qty": sales_qty,
			"sales_value": sales_value,
			"avg_daily_qty": avg_daily_qty,
			"days_of_cover": days_of_cover,
			"status": status,
		})

	# Most urgent first: items with sales ordered by rising days of cover
	# (stockouts at 0), then non-moving items (no sales) last by stock value.
	def sort_key(r):
		no_sales = r["sales_qty"] <= 0
		doc = r["days_of_cover"]
		doc_sort = doc if doc is not None else float("inf")
		return (no_sales, doc_sort, -r["stock_value"])

	rows.sort(key=sort_key)

	summary = get_report_summary(summary_acc, days)
	return rows, summary


def get_report_summary(acc, days):
	portfolio_doc = None
	if acc["sales_value"] > 0:
		portfolio_doc = acc["stock_value"] / (acc["sales_value"] / days)

	return [
		{"label": _("Inventory Value"), "value": acc["stock_value"], "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Sales Value ({0}d)").format(days), "value": acc["sales_value"], "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Portfolio Days of Cover"), "value": flt(portfolio_doc, 1) if portfolio_doc is not None else 0, "datatype": "Float", "indicator": "Green"},
		{"label": _("Stockouts"), "value": acc["stockout"], "datatype": "Int", "indicator": "Red"},
		{"label": _("To Reorder"), "value": acc["reorder"], "datatype": "Int", "indicator": "Orange"},
		{"label": _("Excess"), "value": acc["excess"], "datatype": "Int", "indicator": "Purple"},
		{"label": _("No Recent Sales"), "value": acc["no_sales"], "datatype": "Int", "indicator": "Grey"},
	]


# --------------------------------------------------------------------------- #
# Columns
# --------------------------------------------------------------------------- #

def get_columns(filters, days):
	return [
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 180},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 210},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
		{"label": _("UOM"), "fieldname": "stock_uom", "fieldtype": "Data", "width": 70},
		{"label": _("On-hand Qty"), "fieldname": "on_hand_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Inventory Value"), "fieldname": "stock_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Sales Qty ({0}d)").format(days), "fieldname": "sales_qty", "fieldtype": "Float", "width": 120},
		{"label": _("Sales Value ({0}d)").format(days), "fieldname": "sales_value", "fieldtype": "Currency", "width": 140},
		{"label": _("Avg Daily Qty"), "fieldname": "avg_daily_qty", "fieldtype": "Float", "width": 120, "precision": 2},
		{"label": _("Days of Cover"), "fieldname": "days_of_cover", "fieldtype": "Float", "width": 120, "precision": 1},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 130},
	]
