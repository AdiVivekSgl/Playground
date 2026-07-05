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

Summary card "Production COGS" = Σ over FG items of (Required to Produce x
Item's inventory/stock valuation rate in STOCK_WAREHOUSE) - the cost basis of
what still needs to be manufactured, as distinct from "Pending Net Total"
(sale value, base_rate).
"""

import base64
import json
from datetime import date

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, nowdate

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
def create_production_plan(filters=None):
	"""Creates a DRAFT Production Plan broken down by Sales Order from the
	report's Required to Produce. Recomputed server-side from `filters` (so it
	matches the report, including Buffer edits already persisted to
	Item.safety_stock). Each (item, SO) shortfall becomes an SO-linked po_items
	row; the item's buffer becomes one unlinked row; the rows for an item sum to
	its Required to Produce. Never auto-submitted."""
	filters = frappe.parse_json(filters) if filters else {}

	company = (
		frappe.defaults.get_user_default("Company")
		or frappe.db.get_single_value("Global Defaults", "default_company")
	)
	if not company:
		frappe.throw(_("No default Company found. Please set one before creating a Production Plan."))

	# "Link Sales Orders in Plan" toggle: on -> one po_items row per (item, SO)
	# plus an unlinked buffer row; off -> one simplified row per item carrying the
	# item's whole Required to Produce, no SO linkage. Both totals are identical.
	link_sos = cint(filters.get("link_sales_orders"))

	plan_rows, sales_orders = _compute_plan_rows(filters)

	pp = frappe.new_doc("Production Plan")
	pp.company = company
	if link_sos:
		for so in sales_orders:
			pp.append("sales_orders", so)

	skipped = []
	for pr in plan_rows:
		if not pr.bom_no:
			skipped.append(pr.item_code)
			continue

		if link_sos:
			for so, so_item, qty in pr.so_rows:
				if flt(qty) <= 0:
					continue
				pp.append(
					"po_items",
					{
						"item_code": pr.item_code,
						"bom_no": pr.bom_no,
						"planned_qty": qty,
						"planned_start_date": nowdate(),
						"stock_uom": pr.stock_uom,
						"sales_order": so,
						"sales_order_item": so_item,
					},
				)

			if flt(pr.buffer_qty) > 0:
				pp.append(
					"po_items",
					{
						"item_code": pr.item_code,
						"bom_no": pr.bom_no,
						"planned_qty": pr.buffer_qty,
						"planned_start_date": nowdate(),
						"stock_uom": pr.stock_uom,
						"description": _("Buffer / safety stock (not linked to a Sales Order)"),
					},
				)
		else:
			# Simplified: one row per item = Required to Produce (SO rows + buffer).
			total = sum(flt(q) for _so, _it, q in pr.so_rows) + flt(pr.buffer_qty)
			if total > 0:
				pp.append(
					"po_items",
					{
						"item_code": pr.item_code,
						"bom_no": pr.bom_no,
						"planned_qty": total,
						"planned_start_date": nowdate(),
						"stock_uom": pr.stock_uom,
					},
				)

	if not pp.get("po_items"):
		frappe.throw(
			_("Nothing to produce — every item's Required to Produce is zero, or none have an active default BOM.")
		)

	pp.insert()

	if skipped:
		frappe.msgprint(
			_("Skipped (no active default BOM found): {0}").format(", ".join(sorted(set(skipped)))),
			indicator="orange",
			alert=True,
		)

	return pp.name


def _compute_plan_rows(filters):
	"""Per-item Production Plan rows, reusing the report's own demand/stock
	helpers so the plan reconciles to the Required to Produce column. Returns
	(plan_rows, sales_orders) where each plan row is
	{item_code, bom_no, stock_uom, so_rows: [(sales_order, so_item, qty), ...],
	buffer_qty} and sales_orders is the Production Plan Sales Order child rows for
	every SO actually used. Free stock is netted across the item's SOs FIFO by
	delivery date; the buffer (plus any stock deficit) becomes the unlinked row."""
	unreserved_basis = filters.get("unreserved_basis") or "Only Displayed SOs"

	so_items = get_open_so_items(filters)
	if not so_items:
		return [], []

	fg_items = sorted(set(r.item_code for r in so_items))
	open_sos = get_ordered_open_sos(so_items)

	pending_map = build_pending_map(so_items)
	reserved_map = get_reserved_map(open_sos)
	stock_map = get_stock_map(fg_items)
	stock_reserved_map = get_reserved_in_stock_warehouse_map(open_sos)
	item_map = get_item_map(fg_items)
	so_header = _get_so_header_map(open_sos)

	# Representative SO Item (largest-pending line) per (SO, item) for linkage.
	so_item_name = {}
	so_item_best = {}
	for r in so_items:
		key = (r.sales_order, r.item_code)
		if flt(r.pending_qty) >= so_item_best.get(key, -1.0):
			so_item_best[key] = flt(r.pending_qty)
			so_item_name[key] = r.so_item

	def _sort_key(so):
		sd = so_header.get(so, {}).get("sort_date")
		return getdate(sd) if sd else date.max

	plan_rows = []
	used_sos = set()

	for item in fg_items:
		# Free stock S for this item, respecting the unreserved basis.
		stock = stock_map.get(item) or frappe._dict()
		if unreserved_basis == "Only Displayed SOs":
			reserved_from_stock = stock_reserved_map.get(item, 0.0) or 0.0
		else:
			reserved_from_stock = flt(stock.get("reserved_qty"))
		free_stock = flt(stock.get("actual_qty")) - reserved_from_stock
		available = free_stock if free_stock > 0 else 0.0
		deficit = -free_stock if free_stock < 0 else 0.0

		buffer = flt((item_map.get(item) or frappe._dict()).get("safety_stock"))

		# Per-SO gross demand (pending − reserved), FIFO by delivery date; free
		# stock covers the earliest-due SOs first.
		item_sos = sorted(
			[so for so in open_sos if flt(pending_map.get((so, item), 0.0)) > 0],
			key=_sort_key,
		)
		remaining = available
		total_demand = 0.0
		so_rows = []
		for so in item_sos:
			demand = flt(pending_map.get((so, item), 0.0)) - flt(reserved_map.get((so, item), 0.0))
			if demand <= 0:
				continue
			total_demand += demand
			cover = min(demand, remaining)
			remaining -= cover
			produced = demand - cover
			if produced > 0:
				so_rows.append((so, so_item_name.get((so, item)), produced))

		# remaining is the leftover free stock after covering all demand.
		buffer_row = max(0.0, buffer - remaining) + deficit

		if not so_rows and buffer_row <= 0:
			continue

		item_detail = frappe.db.get_value("Item", item, ["stock_uom", "default_bom"], as_dict=True)
		if not item_detail:
			continue
		bom_no = item_detail.default_bom or frappe.db.get_value(
			"BOM", {"item": item, "is_default": 1, "is_active": 1, "docstatus": 1}, "name"
		)

		for so, _n, _q in so_rows:
			used_sos.add(so)

		plan_rows.append(
			frappe._dict(
				{
					"item_code": item,
					"bom_no": bom_no,
					"stock_uom": item_detail.stock_uom,
					"so_rows": so_rows,
					"buffer_qty": buffer_row,
				}
			)
		)

	sales_orders = [
		{
			"sales_order": so_header[so]["sales_order"],
			"sales_order_date": so_header[so]["sales_order_date"],
			"customer": so_header[so]["customer"],
			"grand_total": so_header[so]["grand_total"],
		}
		for so in open_sos
		if so in used_sos and so in so_header
	]
	return plan_rows, sales_orders


def _get_so_header_map(open_sos):
	"""{so: {sales_order, sales_order_date, customer, grand_total, sort_date}} —
	sort_date is custom_updated_delivery_date, then delivery_date, then
	transaction_date, for FIFO stock allocation."""
	if not open_sos:
		return {}
	has_custom = frappe.db.has_column("Sales Order", CUSTOM_DELIVERY_DATE_FIELD)
	fields = ["name", "transaction_date", "delivery_date", "customer", "base_grand_total"]
	if has_custom:
		fields.append(CUSTOM_DELIVERY_DATE_FIELD)

	rows = frappe.get_all("Sales Order", filters={"name": ["in", open_sos]}, fields=fields)
	out = {}
	for r in rows:
		sort_date = (r.get(CUSTOM_DELIVERY_DATE_FIELD) if has_custom else None) or r.delivery_date or r.transaction_date
		out[r.name] = {
			"sales_order": r.name,
			"sales_order_date": r.transaction_date,
			"customer": r.customer,
			"grand_total": r.base_grand_total,
			"sort_date": sort_date,
		}
	return out


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
	production_cogs = 0.0

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

		# Production COGS: itemwise qty requiring production x inventory
		# (stock valuation) rate - the cost basis of what still needs to be
		# manufactured, as opposed to Pending Net Total (sale value).
		production_cogs += required_to_produce * flt(stock.get("valuation_rate"))

	# SO-value metrics for the summary cards + the "Top 20 SOs by Pending Value
	# & Qty" combination chart. Pending value = pending_qty * base_rate (company
	# currency), pending qty = qty - delivered_qty, both summed per SO.
	total_pending_value, multi_so_customers, top_sos = _value_metrics(
		so_items, so_customer, reserved_map
	)

	report_summary = [
		{
			"label": _("Total Qty to Produce"),
			"value": total_to_produce,
			"datatype": "Float",
			"indicator": "Orange" if total_to_produce else "Green",
		},
		{"label": _("Production COGS"), "value": production_cogs, "datatype": "Currency"},
		{"label": _("Pending Net Total (Displayed SOs)"), "value": total_pending_value, "datatype": "Currency"},
		{"label": _("Customers with >1 Open SO"), "value": multi_so_customers, "datatype": "Int"},
		{"label": _("Items Needing Production"), "value": items_needing, "datatype": "Int"},
		{"label": _("Open Sales Orders"), "value": len(all_open_sos), "datatype": "Int"},
	]

	# The dual-axis (value bars + qty line) chart can't be done with Frappe's
	# built-in frappe-charts, so it's returned as an HTML `message` and drawn
	# with Chart.js by the report's client script (see after_datatable_render).
	message = _combo_chart_message(top_sos, so_customer, customer_name_map)

	return columns, data, message, None, report_summary


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
			soi.name AS so_item,
			so.transaction_date,
			so.customer,
			soi.item_code,
			-- base_rate = line rate in COMPANY currency, so the Pending Net
			-- Total card is in company currency and never mixes currencies.
			soi.base_rate AS rate,
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


def _value_metrics(so_items, so_customer, reserved_map):
	"""Returns (total_pending_value, multi_so_customer_count, top_sos) where:
	  - total_pending_value = Σ pending_qty * rate over every displayed SO line
	    (`rate` is base_rate — company currency)
	  - multi_so_customer_count = customers with more than one open SO here
	  - top_sos = [(sales_order, pending_value, pending_qty, produce_qty), ...]
	    top 20 SOs by pending value, where produce_qty = max(0, SO total pending
	    − SO total reserved)
	"""
	so_pending_value = {}
	so_pending_qty = {}
	for r in so_items:
		so = r.sales_order
		so_pending_value[so] = so_pending_value.get(so, 0.0) + flt(r.pending_qty) * flt(r.rate)
		so_pending_qty[so] = so_pending_qty.get(so, 0.0) + flt(r.pending_qty)
	total_pending_value = sum(so_pending_value.values())

	# Chart line "Qty to produce" per SO = (pending − reserved) for that SO,
	# i.e. the SO's total pending minus its total reserved, clamped at 0.
	so_reserved_qty = {}
	for (so, _item), reserved in reserved_map.items():
		so_reserved_qty[so] = so_reserved_qty.get(so, 0.0) + flt(reserved)
	so_produce_qty = {
		so: max(0.0, so_pending_qty.get(so, 0.0) - so_reserved_qty.get(so, 0.0))
		for so in so_pending_qty
	}

	customer_sos = {}
	for so, customer in so_customer.items():
		customer_sos.setdefault(customer, set()).add(so)
	multi_so_customers = sum(1 for sos in customer_sos.values() if len(sos) > 1)

	ranked = sorted(so_pending_value.items(), key=lambda kv: kv[1], reverse=True)[:20]
	top_sos = [
		(so, value, so_pending_qty.get(so, 0.0), so_produce_qty.get(so, 0.0))
		for so, value in ranked
	]
	return total_pending_value, multi_so_customers, top_sos


def _combo_chart_message(top_sos, so_customer, customer_name_map):
	"""HTML for the top-20 dual-axis chart: pending value (bars, left axis) with
	pending qty (line, right axis) overlaid. The series is base64-encoded into a
	canvas data attribute; the report's client script decodes it and draws with
	Chart.js (frappe-charts can't do a second y-axis). Returns None when empty."""
	if not top_sos:
		return None

	series = {
		"labels": [_so_header_label(so, so_customer, customer_name_map) for so, _v, _q, _p in top_sos],
		"value": [round(v, 2) for _so, v, _q, _p in top_sos],
		"qty": [round(q, 2) for _so, _v, q, _p in top_sos],
		"produce": [round(p, 2) for _so, _v, _q, p in top_sos],
	}
	payload = base64.b64encode(json.dumps(series).encode("utf-8")).decode("ascii")

	return (
		'<div style="display:flex;gap:16px;flex-wrap:wrap;font-size:12px;margin:6px 0 6px;color:var(--text-muted);">'
		'<span><span style="display:inline-block;width:11px;height:11px;border-radius:2px;'
		'background:#2a78d6;vertical-align:middle;"></span> Pending value (left axis)</span>'
		'<span><span style="display:inline-block;width:16px;border-top:2px solid #eb6834;'
		'vertical-align:middle;"></span> Pending qty (right axis)</span>'
		'<span><span style="display:inline-block;width:16px;border-top:2px dashed #199e70;'
		'vertical-align:middle;"></span> Qty to produce (right axis)</span>'
		"</div>"
		'<div style="position:relative;height:380px;width:100%;">'
		'<canvas id="prr-combo-chart" data-series="{0}"></canvas>'
		"</div>"
	).format(payload)


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
	"""On-hand, Bin-reserved qty, and valuation rate per item in STOCK_WAREHOUSE
	only (not summed across all warehouses). `reserved_qty` here is
	Bin.reserved_qty - i.e. ALL reservations of any kind in that warehouse -
	used when the "Total Avlbl Free Stock" basis is All Reservations.
	`valuation_rate` is the item's inventory (stock valuation) rate in that
	warehouse, used for the Production COGS summary card. Returns
	{item_code: {actual_qty, reserved_qty, valuation_rate}}."""
	if not fg_items:
		return {}

	rows = frappe.db.sql(
		"""
		SELECT
			item_code,
			SUM(actual_qty) AS actual_qty,
			SUM(reserved_qty) AS reserved_qty,
			MAX(valuation_rate) AS valuation_rate
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
