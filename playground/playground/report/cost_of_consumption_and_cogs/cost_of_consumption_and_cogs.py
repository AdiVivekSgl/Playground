# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Cost of Consumption and COGS
============================

Derives two manufacturing-cost figures from the Stock Ledger, using a
roll-forward identity rather than tracing every consumption transaction:

    Cost of Consumption = Opening RM + Cost of Purchase - Closing RM
    COGS                = Cost of Consumption + Opening (WIP+FG) - Closing (WIP+FG)

The report only *measures* four things - Opening & Closing stock value per
bucket and Cost of Purchase - and the two cost figures fall out arithmetically.

Buckets (classification "by stock provenance")
----------------------------------------------
Each item is classified from its all-time Stock Ledger inflows, company-wide
(intrinsic to the item, so the warehouse filter does not change its bucket):

* **WIP+FG** - the item has *any* production inflow: a Manufacture/Repack
  ``Stock Entry`` finished line, or a Work Order / Subcontracting Receipt.
* **RM** - otherwise, if it has any purchase inflow (Purchase Receipt/Invoice).
* Items with neither purchase nor production inflow (only opening/reconciliation/
  transfer) fall back to **RM** and are flagged in the ``review`` column.

Production wins; opening-stock / Stock Reconciliation / Material Transfer
receipts are *neutral* and never force an item into WIP+FG.

Valuation basis
---------------
Opening/Closing values are the running company-currency ``stock_value`` of each
item's last Stock Ledger Entry on/before the cut-off (per warehouse, summed) -
the same basis ERPNext's Stock Balance report uses. Opening uses
``posting_date < from_date`` and Closing ``posting_date <= to_date``, so the
period purchases (``BETWEEN from_date AND to_date``) exactly bridge the two and
the identity holds. Cost of Purchase is ``SUM(stock_value_difference)`` of
Purchase Receipt/Invoice inflows (nets purchase returns, same valuation basis).

Any WIP+FG item that was also purchased (dual-sourced, production-wins) has its
purchases kept *out of RM Cost of Purchase* - so the RM consumption identity
stays clean - but *included as an inflow in COGS* (goods bought that are then
sold), and shown as its own KPI so the split stays explainable.
"""

import frappe
from frappe import _
from frappe.utils import flt


RM = "RM"
WIPFG = "WIP+FG"
PURCHASE_VOUCHERS = ("Purchase Receipt", "Purchase Invoice")


def execute(filters=None):
	filters = frappe._dict(filters or {})

	if not (filters.get("from_date") and filters.get("to_date")):
		frappe.msgprint(_("Please set From Date and To Date."), indicator="orange", alert=True)
		return get_columns(), []

	purchased, produced = get_bucket_map(filters)
	opening = get_stock_value_asof(filters, filters.get("from_date"), "<")
	closing = get_stock_value_asof(filters, filters.get("to_date"), "<=")
	purchases = get_purchase_value(filters)

	rows, totals = build_rows(filters, purchased, produced, opening, closing, purchases)
	report_summary = get_report_summary(totals)

	return get_columns(), rows, None, None, report_summary


# --------------------------------------------------------------------------- #
# Shared scope conditions
# --------------------------------------------------------------------------- #

def _scope(filters, params, with_warehouse=True):
	"""Optional company / warehouse / item-group scope.

	Values go into ``params`` (never interpolated); the fragments only name
	columns, mirroring the parameterised style used across this app's reports.
	``with_warehouse`` is False for classification, which is intrinsic to the
	item and must not be narrowed by a warehouse filter.
	"""
	join = ""
	conditions = ""
	if filters.get("company"):
		conditions += " AND sle.company = %(company)s"
		params["company"] = filters.get("company")
	if with_warehouse and filters.get("warehouse"):
		conditions += " AND sle.warehouse = %(warehouse)s"
		params["warehouse"] = filters.get("warehouse")
	if filters.get("item_group"):
		join += " INNER JOIN `tabItem` it ON it.name = sle.item_code"
		conditions += " AND it.item_group = %(item_group)s"
		params["item_group"] = filters.get("item_group")
	return join, conditions


# --------------------------------------------------------------------------- #
# 1. Provenance classification (all-time, company-wide)
# --------------------------------------------------------------------------- #

def get_bucket_map(filters):
	"""Return ``(purchased_items, produced_items)`` as two sets of item_code."""
	params = {}
	join, conditions = _scope(filters, params, with_warehouse=False)

	purchase_rows = frappe.db.sql(
		"""
		SELECT DISTINCT sle.item_code
		FROM `tabStock Ledger Entry` sle
		{join}
		WHERE sle.is_cancelled = 0
			AND sle.actual_qty > 0
			AND sle.voucher_type IN ('Purchase Receipt', 'Purchase Invoice')
			{conditions}
		""".format(join=join, conditions=conditions),
		params,
	)

	production_rows = frappe.db.sql(
		"""
		SELECT DISTINCT sle.item_code
		FROM `tabStock Ledger Entry` sle
		{join}
		WHERE sle.is_cancelled = 0
			AND sle.actual_qty > 0
			AND (
				sle.voucher_type IN ('Work Order', 'Subcontracting Receipt')
				OR (
					sle.voucher_type = 'Stock Entry'
					AND EXISTS (
						SELECT 1 FROM `tabStock Entry` se
						WHERE se.name = sle.voucher_no
							AND se.purpose IN ('Manufacture', 'Repack')
					)
				)
			)
			{conditions}
		""".format(join=join, conditions=conditions),
		params,
	)

	return {r[0] for r in purchase_rows}, {r[0] for r in production_rows}


# --------------------------------------------------------------------------- #
# 2. As-of stock value snapshot (opening / closing)
# --------------------------------------------------------------------------- #

def get_stock_value_asof(filters, as_of, op):
	"""Company-currency stock value per item as of a cut-off date.

	For each item+warehouse the last non-cancelled SLE on/before the cut-off is
	taken (its running ``stock_value``), then summed across warehouses. ``op`` is
	the comparison operator - a controlled literal, never user input.
	"""
	op = "<" if op == "<" else "<="
	params = {"as_of": as_of}
	join, conditions = _scope(filters, params)

	data = frappe.db.sql(
		"""
		SELECT t.item_code AS item_code, SUM(t.stock_value) AS stock_value
		FROM (
			SELECT
				sle.item_code AS item_code,
				sle.stock_value AS stock_value,
				ROW_NUMBER() OVER (
					PARTITION BY sle.item_code, sle.warehouse
					ORDER BY sle.posting_date DESC, sle.posting_time DESC, sle.creation DESC
				) AS rn
			FROM `tabStock Ledger Entry` sle
			{join}
			WHERE sle.is_cancelled = 0
				AND sle.posting_date {op} %(as_of)s
				{conditions}
		) t
		WHERE t.rn = 1
		GROUP BY t.item_code
		""".format(join=join, conditions=conditions, op=op),
		params,
		as_dict=True,
	)
	return {d.item_code: flt(d.stock_value) for d in data}


# --------------------------------------------------------------------------- #
# 3. Period purchases (valuation basis)
# --------------------------------------------------------------------------- #

def get_purchase_value(filters):
	params = {"from_date": filters.get("from_date"), "to_date": filters.get("to_date")}
	join, conditions = _scope(filters, params)

	data = frappe.db.sql(
		"""
		SELECT
			sle.item_code                    AS item_code,
			SUM(sle.stock_value_difference)  AS purchase_value,
			SUM(sle.actual_qty)              AS purchase_qty
		FROM `tabStock Ledger Entry` sle
		{join}
		WHERE sle.is_cancelled = 0
			AND sle.posting_date BETWEEN %(from_date)s AND %(to_date)s
			AND sle.voucher_type IN ('Purchase Receipt', 'Purchase Invoice')
			{conditions}
		GROUP BY sle.item_code
		""".format(join=join, conditions=conditions),
		params,
		as_dict=True,
	)
	return {d.item_code: d for d in data}


# --------------------------------------------------------------------------- #
# 4. Merge + derived columns + bucket subtotals
# --------------------------------------------------------------------------- #

def build_rows(filters, purchased, produced, opening, closing, purchases):
	item_codes = set(opening) | set(closing) | set(purchases)

	names = {}
	if item_codes:
		for it in frappe.get_all(
			"Item",
			filters={"name": ["in", list(item_codes)]},
			fields=["name", "item_name", "item_group"],
		):
			names[it.name] = it

	item_rows = []
	for item_code in item_codes:
		is_produced = item_code in produced
		bucket = WIPFG if is_produced else RM
		flagged = not is_produced and item_code not in purchased

		opening_value = flt(opening.get(item_code))
		closing_value = flt(closing.get(item_code))
		p = purchases.get(item_code)
		purchase_value = flt(p.purchase_value) if p else 0.0

		if bucket == RM:
			movement = opening_value + purchase_value - closing_value
		else:
			movement = opening_value - closing_value

		meta = names.get(item_code)
		item_rows.append({
			"item_code": item_code,
			"item_name": meta.item_name if meta else None,
			"item_group": meta.item_group if meta else None,
			"bucket": bucket,
			"opening_value": opening_value,
			"purchase_value": purchase_value,
			"closing_value": closing_value,
			"movement": movement,
			"review": _("No purchase/production history") if flagged else "",
			"is_subtotal": 0,
		})

	# Bucket totals feed both the subtotal rows and the KPI identities.
	totals = {
		"opening_rm": 0.0, "closing_rm": 0.0, "purchase_rm": 0.0,
		"opening_wipfg": 0.0, "closing_wipfg": 0.0, "purchase_wipfg": 0.0,
		"flagged_count": 0,
	}
	for r in item_rows:
		if r["bucket"] == RM:
			totals["opening_rm"] += r["opening_value"]
			totals["closing_rm"] += r["closing_value"]
			totals["purchase_rm"] += r["purchase_value"]
		else:
			totals["opening_wipfg"] += r["opening_value"]
			totals["closing_wipfg"] += r["closing_value"]
			totals["purchase_wipfg"] += r["purchase_value"]
		if r["review"]:
			totals["flagged_count"] += 1

	totals["cost_of_consumption"] = (
		totals["opening_rm"] + totals["purchase_rm"] - totals["closing_rm"]
	)
	# WIP+FG purchases are a real COGS inflow (goods bought that are then sold),
	# so they belong in COGS even though they are kept out of RM Cost of Purchase
	# to preserve the RM consumption identity.
	totals["cogs"] = (
		totals["cost_of_consumption"]
		+ totals["purchase_wipfg"]
		+ totals["opening_wipfg"]
		- totals["closing_wipfg"]
	)

	# Group by bucket (RM first), highest absolute movement first within a bucket.
	bucket_order = {RM: 0, WIPFG: 1}
	item_rows.sort(key=lambda r: (bucket_order[r["bucket"]], -abs(r["movement"])))

	rows = []
	for bucket in (RM, WIPFG):
		bucket_items = [r for r in item_rows if r["bucket"] == bucket]
		if not bucket_items:
			continue
		rows.extend(bucket_items)
		rows.append(_subtotal_row(bucket, bucket_items))

	return rows, totals


def _subtotal_row(bucket, bucket_items):
	row = {
		"item_code": None,
		"item_name": _("{0} Subtotal").format(bucket),
		"item_group": None,
		"bucket": bucket,
		"is_subtotal": 1,
		"review": "",
	}
	for field in ("opening_value", "purchase_value", "closing_value", "movement"):
		row[field] = sum(r[field] for r in bucket_items)
	return row


# --------------------------------------------------------------------------- #
# 5. KPI summary cards
# --------------------------------------------------------------------------- #

def get_report_summary(totals):
	return [
		{"label": _("Opening RM"), "value": totals["opening_rm"], "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Cost of Purchase (RM)"), "value": totals["purchase_rm"], "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Closing RM"), "value": totals["closing_rm"], "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Cost of Consumption"), "value": totals["cost_of_consumption"], "datatype": "Currency", "indicator": "Green"},
		{"label": _("Opening WIP+FG"), "value": totals["opening_wipfg"], "datatype": "Currency", "indicator": "Orange"},
		{"label": _("Closing WIP+FG"), "value": totals["closing_wipfg"], "datatype": "Currency", "indicator": "Orange"},
		{"label": _("Cost of Goods Sold"), "value": totals["cogs"], "datatype": "Currency", "indicator": "Red"},
		{"label": _("WIP+FG Purchases (in COGS)"), "value": totals["purchase_wipfg"], "datatype": "Currency", "indicator": "Orange"},
		{"label": _("Flagged Items"), "value": totals["flagged_count"], "datatype": "Int", "indicator": "Grey"},
	]


# --------------------------------------------------------------------------- #
# Columns
# --------------------------------------------------------------------------- #

def get_columns():
	return [
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 180},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 140},
		{"label": _("Bucket"), "fieldname": "bucket", "fieldtype": "Data", "width": 90},
		{"label": _("Opening Value"), "fieldname": "opening_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Purchases"), "fieldname": "purchase_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Closing Value"), "fieldname": "closing_value", "fieldtype": "Currency", "width": 130},
		{"label": _("Consumed / Released"), "fieldname": "movement", "fieldtype": "Currency", "width": 150},
		{"label": _("Review"), "fieldname": "review", "fieldtype": "Data", "width": 200},
		{"label": _("is_subtotal"), "fieldname": "is_subtotal", "fieldtype": "Int", "hidden": 1},
	]
