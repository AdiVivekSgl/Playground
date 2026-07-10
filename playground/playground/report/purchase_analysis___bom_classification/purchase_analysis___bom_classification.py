# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Analysis - BOM Classification
======================================

Classifies every submitted Purchase Invoice line as Direct / Indirect / Capital,
so procurement spend can be split by how the material is actually used rather
than by accounting bucket.

Classification (priority order):
  1. Item.is_fixed_asset = 1                         -> Capital
  2. Item.purchase_classification manual override    -> that value
     (only when the custom Select field exists on this site)
  3. Item used in a submitted, active BOM            -> Direct
  4. Otherwise                                       -> Indirect

The "BOM Item" column is a plain Yes/No of whether the item appears in any
submitted+active BOM (independent of the manual override), so you can see when a
classification came from an override vs BOM membership.

Top-of-report KPI cards show Total / Direct / Indirect / Capital spend (with
each category's share) plus input GST, all honouring the active filters.

Notes on portability: `purchase_classification` (Item), `buyer` (Purchase
Invoice) and `tax_amount` (Purchase Invoice Item) are custom/optional columns.
Each is probed with has_column and simply skipped when absent, so the report
runs on any ERPNext v15 site - it just loses that one input where the field
isn't present.
"""

import frappe
from frappe import _
from frappe.utils import flt


# Membership test reused across the classification CASE and the BOM Item flag:
# does this line's item appear in any submitted, active BOM?
_BOM_EXISTS = """
	EXISTS (
		SELECT 1 FROM `tabBOM` bom
		INNER JOIN `tabBOM Item` bi ON bi.parent = bom.name
		WHERE bi.item_code = pii.item_code
			AND bom.docstatus = 1
			AND bom.is_active = 1
	)
"""


def execute(filters=None):
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	columns = get_columns()
	data = get_data(filters)
	report_summary = get_report_summary(data, filters)
	return columns, data, None, None, report_summary


def get_columns():
	return [
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 90},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 150},
		{"label": _("Purchase Invoice"), "fieldname": "purchase_invoice", "fieldtype": "Link", "options": "Purchase Invoice", "width": 130},
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 120},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 170},
		{"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 120},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 80},
		{"label": _("UOM"), "fieldname": "uom", "fieldtype": "Data", "width": 60},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "width": 100},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 110},
		{"label": _("Cost Center"), "fieldname": "cost_center", "fieldtype": "Link", "options": "Cost Center", "width": 130},
		{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 120},
		{"label": _("Expense Account"), "fieldname": "expense_account", "fieldtype": "Link", "options": "Account", "width": 150},
		{"label": _("Category"), "fieldname": "category", "fieldtype": "Data", "width": 100},
		{"label": _("BOM Item"), "fieldname": "is_bom_item", "fieldtype": "Data", "width": 90},
		{"label": _("Buyer"), "fieldname": "buyer", "fieldtype": "Data", "width": 110},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 180},
	]


def _category_case():
	"""Classification CASE expression, including the manual override branch only
	when Item.purchase_classification exists on this site."""
	override = ""
	if frappe.db.has_column("Item", "purchase_classification"):
		override = """
			WHEN item.purchase_classification IN ('Direct', 'Indirect', 'Capital')
				AND item.purchase_classification IS NOT NULL
				THEN item.purchase_classification"""
	return """
		CASE
			WHEN item.is_fixed_asset = 1 THEN 'Capital'{override}
			WHEN {bom_exists} THEN 'Direct'
			ELSE 'Indirect'
		END
	""".format(override=override, bom_exists=_BOM_EXISTS)


def get_data(filters):
	has_buyer = frappe.db.has_column("Purchase Invoice", "buyer")
	buyer_select = "pi.buyer AS buyer" if has_buyer else "NULL AS buyer"

	conditions = ["pi.docstatus = 1"]
	params = {}
	if filters.get("from_date"):
		conditions.append("pi.posting_date >= %(from_date)s")
		params["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("pi.posting_date <= %(to_date)s")
		params["to_date"] = filters["to_date"]
	if filters.get("supplier"):
		conditions.append("pi.supplier = %(supplier)s")
		params["supplier"] = filters["supplier"]
	if filters.get("item_code"):
		conditions.append("pii.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]
	if filters.get("item_group"):
		conditions.append("item.item_group = %(item_group)s")
		params["item_group"] = filters["item_group"]

	query = """
		SELECT
			pi.posting_date,
			pi.supplier,
			pi.name AS purchase_invoice,
			pii.item_code,
			item.item_name,
			item.item_group,
			pii.qty,
			pii.uom,
			pii.rate,
			pii.amount,
			pii.cost_center,
			pii.warehouse,
			pii.expense_account,
			{category_case} AS category,
			CASE WHEN {bom_exists} THEN 'Yes' ELSE 'No' END AS is_bom_item,
			{buyer_select},
			pi.remarks
		FROM `tabPurchase Invoice Item` pii
		INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
		INNER JOIN `tabItem` item ON item.name = pii.item_code
		WHERE {where}
		ORDER BY pi.posting_date DESC, pi.name DESC
	""".format(
		category_case=_category_case(),
		bom_exists=_BOM_EXISTS,
		buyer_select=buyer_select,
		where=" AND ".join(conditions),
	)

	data = frappe.db.sql(query, params, as_dict=True)

	# Category is a computed column, so it can't live in the SQL WHERE. Filtering
	# here (rather than replicating the CASE as a WHERE clause, as the original
	# did) keeps the manual-override branch honoured for the Category filter too.
	category = filters.get("category")
	if category:
		data = [r for r in data if r.category == category]

	return data


def get_report_summary(data, filters):
	"""KPI cards from the already-classified rows (so they always match exactly
	what the filtered table shows), plus input GST for the same filtered set."""
	totals = {"Direct": 0.0, "Indirect": 0.0, "Capital": 0.0}
	for r in data:
		totals[r.category] = totals.get(r.category, 0.0) + flt(r.amount)

	total = sum(totals.values())

	def pct(part):
		return " ({0:.1f}%)".format(part / total * 100) if total else ""

	cards = [
		{"label": _("Total Purchases"), "value": total, "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Direct") + pct(totals["Direct"]), "value": totals["Direct"], "datatype": "Currency", "indicator": "Green"},
		{"label": _("Indirect") + pct(totals["Indirect"]), "value": totals["Indirect"], "datatype": "Currency", "indicator": "Orange"},
		{"label": _("Capital") + pct(totals["Capital"]), "value": totals["Capital"], "datatype": "Currency", "indicator": "Purple"},
	]

	gst = get_total_gst(filters)
	if gst is not None:
		cards.append({"label": _("Input GST / IGST"), "value": gst, "datatype": "Currency", "indicator": "Grey"})

	cards.append(
		{
			"label": _("Direct % of Total"),
			"value": "{0:.1f}%".format(totals["Direct"] / total * 100) if total else "0%",
			"datatype": "Data",
			"indicator": "Green",
		}
	)
	return cards


def get_total_gst(filters):
	"""Total item-wise tax for the filtered, submitted invoices. Returns None (so
	the card is dropped) when the site has no `tax_amount` column on Purchase
	Invoice Item - it's a custom/optional field, not part of stock ERPNext."""
	if not frappe.db.has_column("Purchase Invoice Item", "tax_amount"):
		return None

	conditions = ["pi.docstatus = 1"]
	params = {}
	if filters.get("from_date"):
		conditions.append("pi.posting_date >= %(from_date)s")
		params["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions.append("pi.posting_date <= %(to_date)s")
		params["to_date"] = filters["to_date"]
	if filters.get("supplier"):
		conditions.append("pi.supplier = %(supplier)s")
		params["supplier"] = filters["supplier"]
	if filters.get("item_code"):
		conditions.append("pii.item_code = %(item_code)s")
		params["item_code"] = filters["item_code"]
	if filters.get("item_group"):
		conditions.append("item.item_group = %(item_group)s")
		params["item_group"] = filters["item_group"]

	row = frappe.db.sql(
		"""
		SELECT SUM(pii.tax_amount) AS gst_total
		FROM `tabPurchase Invoice Item` pii
		INNER JOIN `tabPurchase Invoice` pi ON pi.name = pii.parent
		INNER JOIN `tabItem` item ON item.name = pii.item_code
		WHERE {where}
		""".format(where=" AND ".join(conditions)),
		params,
		as_dict=True,
	)
	return flt(row[0].gst_total) if row else 0.0
