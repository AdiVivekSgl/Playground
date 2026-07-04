# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Production Requirement Report
==============================

Column A          : FG Item (any item that appears on at least one open Sales Order)
Per open SO       : a "<SO> Pending" / "<SO> Reserved" column pair
                     - Pending  = SO Item qty not yet delivered (qty - delivered_qty)
                     - Reserved = qty already reserved against that specific SO via
                                  Stock Reservation Entry (docstatus = 1)
Near the end      : Total Avlbl Unreserved Stock (on-hand qty in STOCK_WAREHOUSE
                     only, minus reservations there. The "Unreserved Stock Basis"
                     filter chooses which reservations to net out:
                       - All Reservations  -> Bin.reserved_qty (any reservation)
                       - Only Displayed SOs -> Stock Reservation Entry qty for the
                         Sales Orders shown in this report),
                     Buffer Qty
                     (defaults from Item.safety_stock on load; editable inline in the
                     report grid, but edits are session-only scratch values - they
                     recompute Required to Produce in the browser and are never written
                     back to the Item master. Reload/refresh the report to reset.)
Rightmost         : Required to Produce (calculated)

Required to Produce = max(0, (Total Pending - Total Reserved) - Total Avlbl Unreserved Stock + Buffer Qty)
  i.e. reservations against the displayed SOs net out of BOTH the demand
  (Pending - Reserved) and the available stock, so you only produce for the
  genuinely uncovered shortfall plus the buffer.

NOTE: "Reserved" here relies on Stock Reservation Entry. If that feature isn't in
active use, this column will read 0 for every SO - the report still works, it just
won't be able to net out reservations from demand.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

OPEN_SO_STATUSES = ["Draft", "On Hold", "To Deliver and Bill", "To Bill", "To Deliver"]

# Total Avlbl Stock is measured in this single warehouse only (both the on-hand
# qty and the reservations netted out of it). Change here if the stores
# warehouse is renamed or the report should target a different one.
STOCK_WAREHOUSE = "Stores - FTPL"


@frappe.whitelist()
def create_production_plan(items):
	"""Creates a draft Production Plan pre-populated from the report's Required to Produce
	column. Left unsubmitted so a human reviews/edits/submits it - never auto-submitted."""
	items = frappe.parse_json(items)
	if not items:
		frappe.throw(_("No items with a positive Required to Produce quantity were found."))

	company = (
		frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)
	if not company:
		frappe.throw(_("No default Company found. Please set one before creating a Production Plan."))

	pp = frappe.new_doc("Production Plan")
	pp.company = company

	skipped = []

	for row in items:
		item_code = row.get("item_code")
		qty = flt(row.get("qty"))
		if not item_code or qty <= 0:
			continue

		item = frappe.db.get_value(
			"Item", item_code, ["stock_uom", "default_bom"], as_dict=True
		)
		if not item:
			skipped.append(item_code)
			continue

		bom_no = item.default_bom or frappe.db.get_value(
			"BOM",
			{"item": item_code, "is_default": 1, "is_active": 1, "docstatus": 1},
			"name",
		)
		if not bom_no:
			skipped.append(item_code)
			continue

		pp.append(
			"po_items",
			{
				"item_code": item_code,
				"bom_no": bom_no,
				"planned_qty": qty,
				"planned_start_date": nowdate(),
				"stock_uom": item.stock_uom,
			},
		)

	if not pp.get("po_items"):
		frappe.throw(_("None of the selected items have an active default BOM - could not create a Production Plan."))

	pp.insert()

	if skipped:
		frappe.msgprint(
			_("Skipped (no active default BOM found): {0}").format(", ".join(skipped)),
			indicator="orange",
			alert=True,
		)

	return pp.name


def execute(filters=None):
	filters = filters or {}
	unreserved_basis = filters.get("unreserved_basis") or "All Reservations"

	so_items = get_open_so_items(filters)
	if not so_items:
		return get_base_columns(), []

	fg_items = sorted(set(row.item_code for row in so_items))
	open_sos = get_ordered_open_sos(so_items)

	pending_map = build_pending_map(so_items)
	reserved_map = get_reserved_map(open_sos)
	stock_map = get_stock_map(fg_items)
	stock_reserved_map = get_reserved_in_stock_warehouse_map(open_sos)
	buffer_map = get_buffer_map(fg_items)

	columns = get_base_columns()
	for so in open_sos:
		columns.append({
			"label": _("{0} Pending").format(so),
			"fieldname": "pending_{0}".format(frappe.scrub(so)),
			"fieldtype": "Float",
			"width": 110,
		})
		columns.append({
			"label": _("{0} Reserved").format(so),
			"fieldname": "reserved_{0}".format(frappe.scrub(so)),
			"fieldtype": "Float",
			"width": 110,
		})

	columns += [
		{"label": _("Total Avlbl Unreserved Stock"), "fieldname": "total_avlbl_stock", "fieldtype": "Float", "width": 180},
		{"label": _("Buffer Qty"), "fieldname": "buffer_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Required to Produce"), "fieldname": "required_to_produce", "fieldtype": "Float", "width": 150},
	]

	data = []
	for item in fg_items:
		row = {"item_code": item}
		total_pending = 0.0
		total_reserved = 0.0

		for so in open_sos:
			p = pending_map.get((so, item), 0.0)
			r = reserved_map.get((so, item), 0.0)
			row["pending_{0}".format(frappe.scrub(so))] = p
			row["reserved_{0}".format(frappe.scrub(so))] = r
			total_pending += p
			total_reserved += r

		# Total Avlbl Unreserved Stock: on-hand in the stores warehouse minus
		# reservations there. The "unreserved_basis" filter chooses which
		# reservations to net out:
		#   - "All Reservations": every reservation in the warehouse
		#     (Bin.reserved_qty) — truly uncommitted free stock.
		#   - "Only Displayed SOs": only reservations tied to the Sales Orders
		#     shown in this report.
		stock = stock_map.get(item) or frappe._dict()
		actual_qty = flt(stock.get("actual_qty"))
		if unreserved_basis == "Only Displayed SOs":
			reserved_from_stock = stock_reserved_map.get(item, 0.0) or 0.0
		else:  # "All Reservations" (default)
			reserved_from_stock = flt(stock.get("reserved_qty"))
		total_avlbl_stock = actual_qty - reserved_from_stock

		buffer_qty = buffer_map.get(item, 0.0) or 0.0

		unfulfilled_demand = total_pending - total_reserved
		required_to_produce = max(0.0, unfulfilled_demand - total_avlbl_stock + buffer_qty)

		row["total_avlbl_stock"] = total_avlbl_stock
		row["buffer_qty"] = buffer_qty
		row["required_to_produce"] = required_to_produce

		data.append(row)

	return columns, data


def get_base_columns():
	return [
		{
			"label": _("FG Item"),
			"fieldname": "item_code",
			"fieldtype": "Link",
			"options": "Item",
			"width": 220,
		},
	]


def get_open_so_items(filters):
	conditions = ""
	values = {"statuses": OPEN_SO_STATUSES}

	if filters.get("item_code"):
		conditions += " AND soi.item_code = %(item_code)s"
		values["item_code"] = filters.get("item_code")

	if filters.get("customer"):
		conditions += " AND so.customer = %(customer)s"
		values["customer"] = filters.get("customer")

	return frappe.db.sql(
		"""
		SELECT
			soi.parent AS sales_order,
			so.transaction_date,
			so.customer,
			soi.item_code,
			(soi.qty - soi.delivered_qty) AS pending_qty
		FROM `tabSales Order Item` soi
		INNER JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE so.docstatus = 1
			AND so.status IN %(statuses)s
			AND (soi.qty - soi.delivered_qty) > 0
			{conditions}
		ORDER BY so.transaction_date ASC, soi.parent ASC
		""".format(conditions=conditions),
		values,
		as_dict=True,
	)


def get_ordered_open_sos(so_items):
	seen = []
	seen_set = set()
	for row in so_items:
		if row.sales_order not in seen_set:
			seen.append(row.sales_order)
			seen_set.add(row.sales_order)
	return seen


def build_pending_map(so_items):
	pending_map = {}
	for row in so_items:
		key = (row.sales_order, row.item_code)
		pending_map[key] = pending_map.get(key, 0.0) + row.pending_qty
	return pending_map


def get_reserved_map(open_sos):
	if not open_sos:
		return {}

	if not frappe.db.table_exists("Stock Reservation Entry"):
		return {}

	# ERPNext 15 Stock Reservation Entry has no `against_sales_order` column —
	# reservations are stored generically as voucher_type/voucher_no, so a
	# Sales Order reservation is voucher_type = "Sales Order", voucher_no = <SO>.
	rows = frappe.db.sql(
		"""
		SELECT
			sre.voucher_no AS sales_order,
			sre.item_code,
			SUM(sre.reserved_qty) AS reserved_qty
		FROM `tabStock Reservation Entry` sre
		WHERE sre.docstatus = 1
			AND sre.voucher_type = 'Sales Order'
			AND sre.voucher_no IN %(sos)s
		GROUP BY sre.voucher_no, sre.item_code
		""",
		{"sos": open_sos},
		as_dict=True,
	)
	return {(r.sales_order, r.item_code): r.reserved_qty or 0.0 for r in rows}


def get_stock_map(fg_items):
	"""On-hand and Bin-reserved qty per item in STOCK_WAREHOUSE only (not
	summed across all warehouses). `reserved_qty` here is Bin.reserved_qty —
	i.e. ALL reservations of any kind in that warehouse — used when the
	"Total Avlbl Unreserved Stock" basis is All Reservations. Returns
	{item_code: {actual_qty, reserved_qty}}."""
	if not fg_items:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT
			item_code,
			SUM(actual_qty) AS actual_qty,
			SUM(reserved_qty) AS reserved_qty
		FROM `tabBin`
		WHERE item_code IN %(items)s
			AND warehouse = %(warehouse)s
		GROUP BY item_code
		""",
		{"items": fg_items, "warehouse": STOCK_WAREHOUSE},
		as_dict=True,
	)
	return {r.item_code: r for r in rows}


def get_reserved_in_stock_warehouse_map(open_sos):
	"""Qty reserved against the displayed (open) Sales Orders, restricted to
	STOCK_WAREHOUSE. This is what gets netted out of that warehouse's on-hand
	qty to give Total Avlbl Stock — i.e. stock physically in the stores that is
	already earmarked for the Sales Orders shown in this report. Returns
	{item_code: reserved_qty}. Distinct from get_reserved_map, which is
	per-SO and across all warehouses (used for the Reserved columns and the
	demand side of Required to Produce)."""
	if not open_sos or not frappe.db.table_exists("Stock Reservation Entry"):
		return {}

	rows = frappe.db.sql(
		"""
		SELECT
			sre.item_code,
			SUM(sre.reserved_qty) AS reserved_qty
		FROM `tabStock Reservation Entry` sre
		WHERE sre.docstatus = 1
			AND sre.voucher_type = 'Sales Order'
			AND sre.voucher_no IN %(sos)s
			AND sre.warehouse = %(warehouse)s
		GROUP BY sre.item_code
		""",
		{"sos": open_sos, "warehouse": STOCK_WAREHOUSE},
		as_dict=True,
	)
	return {r.item_code: r.reserved_qty or 0.0 for r in rows}


def get_buffer_map(fg_items):
	if not fg_items:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT name AS item_code, safety_stock
		FROM `tabItem`
		WHERE name IN %(items)s
		""",
		{"items": fg_items},
		as_dict=True,
	)
	return {r.item_code: r.safety_stock or 0.0 for r in rows}
