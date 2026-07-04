# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Production Requirement Report
==============================

Layout (left -> right):
  FG Item, Item Name, Total Avlbl Free Stock, Buffer Qty, Required to Produce,
  then one "Customer Name (SO No.) - Pending" / "... - Reserved" column pair per
  displayed open Sales Order.

  - Pending  = SO Item qty not yet delivered (qty - delivered_qty)
  - Reserved = qty reserved against that SO via Stock Reservation Entry (docstatus 1)
  - Total Avlbl Free Stock = on-hand in STOCK_WAREHOUSE minus reservations there.
      "Unreserved Stock Basis" filter:
        All Reservations   -> Bin.reserved_qty (any reservation)
        Only Displayed SOs -> Stock Reservation Entry qty for the shown SOs
  - Buffer Qty defaults from Item.safety_stock and is editable inline; an edit
    recomputes Required to Produce AND is saved back to Item.safety_stock
    (see update_buffer_qty, guarded by Item write permission).
  - Required to Produce = max(0, (Total Pending - Total Reserved)
                                  - Total Avlbl Free Stock + Buffer Qty)

Filters:
  - FG Item, Customer
  - Date range (From/To) applied to the Sales Order field chosen by "Date Basis":
        Document Creation Date       -> transaction_date
        Delivery Date                -> delivery_date
        Custom Updated Delivery Date -> custom_updated_delivery_date
    (falls back to transaction_date if the chosen column doesn't exist)
  - Unreserved Stock Basis (see above)
  - Hide Fulfilled SOs: hide the column pair for any SO with no shortfall
    (reserved >= pending on every line). Purely visual - Required to Produce
    still considers every open SO.

NOTE: "Reserved" relies on Stock Reservation Entry. If that feature isn't in
active use, Reserved reads 0 for every SO - the report still works, it just
won't net reservations out of demand.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

OPEN_SO_STATUSES = ["Draft", "On Hold", "To Deliver and Bill", "To Bill", "To Deliver"]

# Total Avlbl Free Stock is measured in this single warehouse only (both the
# on-hand qty and the reservations netted out of it). Change here if the stores
# warehouse is renamed or the report should target a different one.
STOCK_WAREHOUSE = "Stores - FTPL"

# The custom "updated delivery date" field on Sales Order (from Customize Form).
CUSTOM_DELIVERY_DATE_FIELD = "custom_updated_delivery_date"

# "Date Basis" filter value -> Sales Order (header) date column it filters on.
DATE_BASIS_FIELD = {
	"Document Creation Date": "transaction_date",
	"Delivery Date": "delivery_date",
	"Custom Updated Delivery Date": CUSTOM_DELIVERY_DATE_FIELD,
}


@frappe.whitelist()
def update_buffer_qty(item_code, buffer_qty):
	"""Persist an inline Buffer Qty edit back to Item.safety_stock. Guarded by
	Item write permission. Uses db.set_value (single field, no full Item
	revalidation) since safety_stock is a plain numeric field."""
	if not frappe.has_permission("Item", "write", doc=item_code):
		frappe.throw(
			_("You are not permitted to edit Item {0}.").format(item_code),
			frappe.PermissionError,
		)
	buffer_qty = flt(buffer_qty)
	if buffer_qty < 0:
		frappe.throw(_("Buffer Qty cannot be negative."))
	frappe.db.set_value("Item", item_code, "safety_stock", buffer_qty)
	return buffer_qty


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
	unreserved_basis = filters.get("unreserved_basis") or "Only Displayed SOs"

	so_items = get_open_so_items(filters)
	if not so_items:
		return get_summary_columns(), []

	fg_items = sorted(set(row.item_code for row in so_items))
	all_open_sos = get_ordered_open_sos(so_items)

	pending_map = build_pending_map(so_items)
	reserved_map = get_reserved_map(all_open_sos)
	stock_map = get_stock_map(fg_items)
	stock_reserved_map = get_reserved_in_stock_warehouse_map(all_open_sos)
	item_map = get_item_map(fg_items)
	customer_name_map = get_customer_name_map(so_items)
	so_customer = {row.sales_order: row.customer for row in so_items}

	# "Hide Fulfilled SOs" is visual only: it drops the column pair for SOs that
	# have no shortfall, but every open SO still counts in the totals below.
	display_sos = all_open_sos
	if cint(filters.get("hide_fulfilled")):
		display_sos = [
			so for so in all_open_sos
			if _so_has_shortfall(so, so_items, pending_map, reserved_map)
		]

	columns = get_summary_columns()
	for so in display_sos:
		header = _so_header_label(so, so_customer, customer_name_map)
		columns.append({
			"label": _("{0} - Pending").format(header),
			"fieldname": "pending_{0}".format(frappe.scrub(so)),
			"fieldtype": "Float",
			"width": 140,
		})
		columns.append({
			"label": _("{0} - Reserved").format(header),
			"fieldname": "reserved_{0}".format(frappe.scrub(so)),
			"fieldtype": "Float",
			"width": 140,
		})

	data = []
	total_to_produce = 0.0
	items_needing = 0

	for item in fg_items:
		details = item_map.get(item) or frappe._dict()
		row = {"item_code": item, "item_name": details.get("item_name")}

		total_pending = 0.0
		total_reserved = 0.0
		# Populate pending/reserved for EVERY open SO (including hidden ones) so
		# the inline Buffer recompute in the browser sums the same set the server
		# did. Only the displayed SOs get a column, but all values ride in the row.
		for so in all_open_sos:
			p = pending_map.get((so, item), 0.0)
			r = reserved_map.get((so, item), 0.0)
			row["pending_{0}".format(frappe.scrub(so))] = p
			row["reserved_{0}".format(frappe.scrub(so))] = r
			total_pending += p
			total_reserved += r

		# Total Avlbl Free Stock: on-hand in the stores warehouse minus
		# reservations there, per the "unreserved_basis" filter.
		stock = stock_map.get(item) or frappe._dict()
		actual_qty = flt(stock.get("actual_qty"))
		if unreserved_basis == "Only Displayed SOs":
			reserved_from_stock = stock_reserved_map.get(item, 0.0) or 0.0
		else:  # "All Reservations" (default)
			reserved_from_stock = flt(stock.get("reserved_qty"))
		total_avlbl_stock = actual_qty - reserved_from_stock

		buffer_qty = flt(details.get("safety_stock"))
		unfulfilled_demand = total_pending - total_reserved
		required_to_produce = max(0.0, unfulfilled_demand - total_avlbl_stock + buffer_qty)

		row["total_avlbl_stock"] = total_avlbl_stock
		row["buffer_qty"] = buffer_qty
		row["required_to_produce"] = required_to_produce
		data.append(row)

		total_to_produce += required_to_produce
		if required_to_produce > 0:
			items_needing += 1

	# SO-value metrics for the summary cards + the "Top 5 SOs by Pending Value"
	# chart. Pending value = pending_qty * rate, summed per SO across its items.
	total_pending_value, multi_so_customers, top5 = _value_metrics(so_items, so_customer)

	report_summary = [
		{
			"label": _("Total Qty to Produce"),
			"value": total_to_produce,
			"datatype": "Float",
			"indicator": "Orange" if total_to_produce else "Green",
		},
		{"label": _("Pending Net Total (Displayed SOs)"), "value": total_pending_value, "datatype": "Currency"},
		{"label": _("Customers with >1 Open SO"), "value": multi_so_customers, "datatype": "Int"},
		{"label": _("Items Needing Production"), "value": items_needing, "datatype": "Int"},
		{"label": _("Open Sales Orders"), "value": len(all_open_sos), "datatype": "Int"},
	]

	chart = None
	if top5:
		chart = {
			"type": "bar",
			"data": {
				"labels": [_so_header_label(so, so_customer, customer_name_map) for so, _v in top5],
				"datasets": [{"name": _("Pending Value"), "values": [round(v, 2) for _so, v in top5]}],
			},
			"colors": ["#7cd6fd"],
			"axisOptions": {"shortenYAxisNumbers": 1},
			"title": _("Top 5 Sales Orders by Pending Value"),
		}

	return columns, data, None, chart, report_summary


def get_summary_columns():
	"""The item + calculated summary columns, pinned to the left of the report
	(immediately after the item) so the actionable numbers are seen first."""
	return [
		{"label": _("FG Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 150},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 220},
		{"label": _("Total Avlbl Free Stock"), "fieldname": "total_avlbl_stock", "fieldtype": "Float", "width": 170},
		{"label": _("Buffer Qty"), "fieldname": "buffer_qty", "fieldtype": "Float", "width": 100},
		{"label": _("Required to Produce"), "fieldname": "required_to_produce", "fieldtype": "Float", "width": 160},
	]


def _resolve_date_field(date_basis):
	"""Map the Date Basis filter to a Sales Order column, guarding against a
	custom field that doesn't exist on this site (would otherwise be invalid
	SQL). The values come from the fixed DATE_BASIS_FIELD map, so interpolating
	the result into SQL is safe."""
	field = DATE_BASIS_FIELD.get(date_basis or "Document Creation Date", "transaction_date")
	if not frappe.db.has_column("Sales Order", field):
		return "transaction_date"
	return field


def get_open_so_items(filters):
	conditions = ""
	values = {"statuses": OPEN_SO_STATUSES}

	# Submitted only by default. "Include Draft SOs" also pulls docstatus 0
	# (status "Draft"); cancelled (docstatus 2) is always excluded.
	docstatus_clause = (
		"so.docstatus IN (0, 1)" if cint(filters.get("include_draft")) else "so.docstatus = 1"
	)

	if filters.get("item_code"):
		conditions += " AND soi.item_code = %(item_code)s"
		values["item_code"] = filters.get("item_code")

	if filters.get("customer"):
		conditions += " AND so.customer = %(customer)s"
		values["customer"] = filters.get("customer")

	date_field = _resolve_date_field(filters.get("date_basis"))
	if filters.get("from_date"):
		conditions += " AND so.{0} >= %(from_date)s".format(date_field)
		values["from_date"] = filters.get("from_date")
	if filters.get("to_date"):
		conditions += " AND so.{0} <= %(to_date)s".format(date_field)
		values["to_date"] = filters.get("to_date")

	return frappe.db.sql(
		"""
		SELECT
			soi.parent AS sales_order,
			so.transaction_date,
			so.customer,
			soi.item_code,
			soi.rate AS rate,
			(soi.qty - soi.delivered_qty) AS pending_qty
		FROM `tabSales Order Item` soi
		INNER JOIN `tabSales Order` so ON so.name = soi.parent
		WHERE {docstatus_clause}
			AND so.status IN %(statuses)s
			AND (soi.qty - soi.delivered_qty) > 0
			{conditions}
		ORDER BY so.{date_field} ASC, soi.parent ASC
		""".format(docstatus_clause=docstatus_clause, conditions=conditions, date_field=date_field),
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


def _so_has_shortfall(so, so_items, pending_map, reserved_map):
	"""True if any line on this SO still has pending > reserved (i.e. a real
	production shortfall). Used by the "Hide Fulfilled SOs" toggle."""
	for r in so_items:
		if r.sales_order != so:
			continue
		pending = flt(pending_map.get((so, r.item_code), 0.0))
		reserved = flt(reserved_map.get((so, r.item_code), 0.0))
		if pending - reserved > 0.0001:
			return True
	return False


def _so_header_label(so, so_customer, customer_name_map):
	"""Column header as 'Customer Name (SO No.)'."""
	customer = so_customer.get(so)
	name = customer_name_map.get(customer) or customer
	return "{0} ({1})".format(name, so) if name else so


def _value_metrics(so_items, so_customer):
	"""Returns (total_pending_value, multi_so_customer_count, top5) where:
	  - total_pending_value = Σ pending_qty * rate over every displayed SO line
	  - multi_so_customer_count = customers with more than one open SO here
	  - top5 = [(sales_order, pending_value), ...] top 5 SOs by pending value
	"""
	so_pending_value = {}
	for r in so_items:
		so_pending_value[r.sales_order] = (
			so_pending_value.get(r.sales_order, 0.0) + flt(r.pending_qty) * flt(r.rate)
		)
	total_pending_value = sum(so_pending_value.values())

	customer_sos = {}
	for so, customer in so_customer.items():
		customer_sos.setdefault(customer, set()).add(so)
	multi_so_customers = sum(1 for sos in customer_sos.values() if len(sos) > 1)

	top5 = sorted(so_pending_value.items(), key=lambda kv: kv[1], reverse=True)[:5]
	return total_pending_value, multi_so_customers, top5


def get_reserved_map(open_sos):
	if not open_sos:
		return {}

	if not frappe.db.table_exists("Stock Reservation Entry"):
		return {}

	# ERPNext 15 Stock Reservation Entry has no `against_sales_order` column -
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
	summed across all warehouses). `reserved_qty` here is Bin.reserved_qty -
	i.e. ALL reservations of any kind in that warehouse - used when the
	"Total Avlbl Free Stock" basis is All Reservations. Returns
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
	STOCK_WAREHOUSE. Netted out of that warehouse's on-hand qty when the
	Unreserved Stock Basis is "Only Displayed SOs". Returns
	{item_code: reserved_qty}."""
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


def get_customer_name_map(so_items):
	"""{customer_id: customer_name} for the customers on the shown SOs, used to
	build 'Customer Name (SO No.)' column headers."""
	customers = sorted({r.customer for r in so_items if r.customer})
	if not customers:
		return {}
	rows = frappe.db.sql(
		"""SELECT name, customer_name FROM `tabCustomer` WHERE name IN %(c)s""",
		{"c": customers},
		as_dict=True,
	)
	return {r.name: r.customer_name for r in rows}


def get_item_map(fg_items):
	"""{item_code: {item_name, safety_stock}} for the FG items in the report."""
	if not fg_items:
		return {}
	rows = frappe.db.sql(
		"""SELECT name AS item_code, item_name, safety_stock FROM `tabItem` WHERE name IN %(items)s""",
		{"items": fg_items},
		as_dict=True,
	)
	return {r.item_code: r for r in rows}
