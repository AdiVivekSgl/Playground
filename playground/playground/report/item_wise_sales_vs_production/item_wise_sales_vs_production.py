# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Item-wise Sales vs Production
=============================

A single item-wise view that puts sales and production side by side, so each
commercially-active item can be read as "how much did we sell vs how much did we
make, and what is that production worth at our realized selling price".

Row universe (the "predefined filter")
--------------------------------------
Rows are every item with at least one *submitted* Sales Invoice line whose
``posting_date >= sold_since`` (default 1 April 2026). This anchors the report to
items that are actually being sold; the date is exposed as an (editable) filter.

Metric columns (over a *separate* date range)
---------------------------------------------
All seven columns are computed over the user-chosen ``from_date``..``to_date`` range,
independent of ``sold_since``. So the same item list can be compared across periods.

* **Sale Qty / Sale Value** - ``Sales Invoice Item.qty`` / ``base_net_amount``
  (company currency, tax-exclusive Net Total basis - same field the Sales Pivot
  Matrix uses as "Net Sales Amount").
* **COGS** - ``Sale Qty x current Bin valuation_rate``. A *current-valuation
  estimate*, the same COGS basis used elsewhere in this app
  (``production_requirement_report.get_stock_map``), not the historical cost of
  each specific sale.
* **Produced Qty / Production Value** - the finished-good line of Manufacture
  ``Stock Entry`` documents in the range (``qty`` / ``amount``). Stock Entries are
  dated and carry the FG valuation, unlike the cumulative, undated
  ``Work Order.produced_qty``.
* **Projected Sale Value** - ``Produced Qty x avg realized sale price``, where the
  average price is ``Sale Value / Sale Qty`` for that item in the range. Items
  produced but not sold in the range project to 0.

Money is aggregated in company currency (``base_*`` fields).
"""

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if not (filters.get("sold_since") and filters.get("from_date") and filters.get("to_date")):
		frappe.msgprint(
			_("Please set Sold Since, From Date and To Date."),
			indicator="orange",
			alert=True,
		)
		return get_columns(), []

	universe = get_item_universe(filters)
	if not universe:
		return get_columns(), []

	sales = get_sales(filters)
	production = get_production(filters)
	valuation = get_valuation_map(filters)

	rows = build_rows(universe, sales, production, valuation)
	return get_columns(), rows


# --------------------------------------------------------------------------- #
# Shared scope conditions
# --------------------------------------------------------------------------- #

def _sales_scope(filters, params):
	"""Optional header/item scope shared by the universe and sales queries.

	Values go into ``params`` (never interpolated) - the fragments only name
	columns, mirroring the parameterised style used across this app's reports.
	"""
	conditions = ""
	if filters.get("company"):
		conditions += " AND si.company = %(company)s"
		params["company"] = filters.get("company")
	if filters.get("item_group"):
		conditions += " AND sii.item_group = %(item_group)s"
		params["item_group"] = filters.get("item_group")
	if filters.get("item_code"):
		conditions += " AND sii.item_code = %(item_code)s"
		params["item_code"] = filters.get("item_code")
	return conditions


# --------------------------------------------------------------------------- #
# 1. Row universe - items sold since `sold_since`
# --------------------------------------------------------------------------- #

def get_item_universe(filters):
	params = {"sold_since": filters.get("sold_since")}
	conditions = _sales_scope(filters, params)

	rows = frappe.db.sql(
		"""
		SELECT DISTINCT sii.item_code
		FROM `tabSales Invoice` si
		INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE si.docstatus = 1
			AND si.posting_date >= %(sold_since)s
			{conditions}
		""".format(conditions=conditions),
		params,
	)
	return [r[0] for r in rows]


# --------------------------------------------------------------------------- #
# 2. Sales metrics over the date range
# --------------------------------------------------------------------------- #

def get_sales(filters):
	params = {"from_date": filters.get("from_date"), "to_date": filters.get("to_date")}
	conditions = _sales_scope(filters, params)

	data = frappe.db.sql(
		"""
		SELECT
			sii.item_code               AS item_code,
			MAX(sii.item_name)          AS item_name,
			SUM(sii.qty)                AS sale_qty,
			SUM(sii.base_net_amount)    AS sale_value
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
# 3. Production metrics over the date range (Manufacture Stock Entries)
# --------------------------------------------------------------------------- #

def get_production(filters):
	params = {"from_date": filters.get("from_date"), "to_date": filters.get("to_date")}

	conditions = ""
	if filters.get("company"):
		conditions += " AND se.company = %(company)s"
		params["company"] = filters.get("company")
	if filters.get("item_code"):
		conditions += " AND sed.item_code = %(item_code)s"
		params["item_code"] = filters.get("item_code")

	data = frappe.db.sql(
		"""
		SELECT
			sed.item_code           AS item_code,
			SUM(sed.qty)            AS produced_qty,
			SUM(sed.amount)         AS production_value
		FROM `tabStock Entry` se
		INNER JOIN `tabStock Entry Detail` sed ON sed.parent = se.name
		WHERE se.docstatus = 1
			AND se.purpose = 'Manufacture'
			AND sed.is_finished_item = 1
			AND se.posting_date BETWEEN %(from_date)s AND %(to_date)s
			{conditions}
		GROUP BY sed.item_code
		""".format(conditions=conditions),
		params,
		as_dict=True,
	)
	return {d.item_code: d for d in data}


# --------------------------------------------------------------------------- #
# 4. Current valuation rate per item (COGS basis)
# --------------------------------------------------------------------------- #

def get_valuation_map(filters):
	"""Per-item current valuation rate, weighted across Bins.

	Weighted average (``SUM(stock_value) / SUM(actual_qty)``) is more robust than a
	single Bin's ``valuation_rate`` when an item lives in several warehouses; when
	the item currently has no positive stock we fall back to the max recorded rate.
	An optional Warehouse filter narrows the basis to one warehouse; an optional
	Company filter restricts to that company's warehouses.
	"""
	params = {}
	joins = ""
	conditions = ""

	if filters.get("warehouse"):
		conditions += " AND b.warehouse = %(warehouse)s"
		params["warehouse"] = filters.get("warehouse")
	elif filters.get("company"):
		joins += " INNER JOIN `tabWarehouse` w ON w.name = b.warehouse"
		conditions += " AND w.company = %(company)s"
		params["company"] = filters.get("company")

	data = frappe.db.sql(
		"""
		SELECT
			b.item_code AS item_code,
			CASE WHEN SUM(b.actual_qty) > 0
				THEN SUM(b.stock_value) / SUM(b.actual_qty)
				ELSE MAX(b.valuation_rate) END AS valuation_rate
		FROM `tabBin` b
		{joins}
		WHERE 1 = 1
			{conditions}
		GROUP BY b.item_code
		""".format(joins=joins, conditions=conditions),
		params,
		as_dict=True,
	)
	return {d.item_code: flt(d.valuation_rate) for d in data}


# --------------------------------------------------------------------------- #
# 5. Merge + derived columns + grand total
# --------------------------------------------------------------------------- #

def build_rows(universe, sales, production, valuation):
	rows = []
	for item_code in universe:
		s = sales.get(item_code)
		p = production.get(item_code)

		sale_qty = flt(s.sale_qty) if s else 0.0
		sale_value = flt(s.sale_value) if s else 0.0
		produced_qty = flt(p.produced_qty) if p else 0.0
		production_value = flt(p.production_value) if p else 0.0

		cogs = sale_qty * valuation.get(item_code, 0.0)
		avg_sale_rate = (sale_value / sale_qty) if sale_qty else 0.0
		projected_sale_value = produced_qty * avg_sale_rate

		rows.append({
			"item_code": item_code,
			"item_name": (s.item_name if s and s.item_name else None)
				or frappe.db.get_value("Item", item_code, "item_name"),
			"sale_qty": sale_qty,
			"sale_value": sale_value,
			"cogs": cogs,
			"produced_qty": produced_qty,
			"production_value": production_value,
			"projected_sale_value": projected_sale_value,
			"is_total": 0,
		})

	# Highest sales first - the useful default for triage.
	rows.sort(key=lambda r: r["sale_value"], reverse=True)

	if rows:
		total = {
			"item_code": None,
			"item_name": _("Grand Total"),
			"is_total": 1,
		}
		for field in ("sale_qty", "sale_value", "cogs", "produced_qty",
					   "production_value", "projected_sale_value"):
			total[field] = sum(r[field] for r in rows)
		rows.append(total)

	return rows


# --------------------------------------------------------------------------- #
# Columns
# --------------------------------------------------------------------------- #

def get_columns():
	return [
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		{"label": _("Sale Qty"), "fieldname": "sale_qty", "fieldtype": "Float", "width": 110},
		{"label": _("Sale Value"), "fieldname": "sale_value", "fieldtype": "Currency", "width": 130},
		{"label": _("COGS"), "fieldname": "cogs", "fieldtype": "Currency", "width": 130},
		{"label": _("Produced Qty"), "fieldname": "produced_qty", "fieldtype": "Float", "width": 120},
		{"label": _("Production Value"), "fieldname": "production_value", "fieldtype": "Currency", "width": 140},
		{"label": _("Projected Sale Value"), "fieldname": "projected_sale_value", "fieldtype": "Currency", "width": 160},
	]
