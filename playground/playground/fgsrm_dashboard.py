# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
FG Stock Reservation Manager - Dashboard
========================================

The sales-fulfilment / dispatch dashboard behind the FGSRM report's "Dashboard"
tab: number cards, an ageing split, and two focus lists (Top Blocking Items and
Overdue Sales Orders).

DELIBERATELY STANDALONE. Nothing here imports or touches the FGSRM report's
execute() path, and the one public entry point (get_dashboard_metrics) takes a
plain filters dict and returns a plain JSON-serialisable dict. That's so this can
later be lifted into its own Frappe Dashboard / Workspace page without moving any
logic - a new page would call the same function with the same filters and render
the same payload. Keep it that way: no report/DataTable assumptions in here.

Consistency with the report
---------------------------
Every figure is computed from the SAME helpers the report itself uses
(get_open_so_items, get_line_reserved_map, get_stock_map,
compute_so_qualification_flags), on the SAME "Unreserved Stock Basis", so the
cards always reconcile with the table. The production requirement is re-derived
here rather than imported from _suggested_prodn_by_item - the dashboard needs the
per-item breakdown (blocked SO count, value at risk) that helper doesn't return -
but by the identical netting, so the two can't disagree. See _rollup_by_item.

Only the real filters apply (item / customer / sales order / date range / date
basis / unreserved basis). The display-only toggles - only_unreserved,
group_by_so, view_mode - are ignored, exactly as _suggested_prodn_by_item already
does, so the cards always describe true open demand rather than whatever subset
happens to be on screen.

Money basis
-----------
Sale value is EX-TAX: pending_qty x base_net_rate (company currency). Production
requirement is reported both ways - the revenue it unlocks (ex-tax sale value)
and what it costs to make (qty x stock valuation_rate, the same COGS basis the
Production Requirement Report already uses).
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from playground.playground.report.production_requirement_report.production_requirement_report import (
	STOCK_WAREHOUSE,
	get_open_so_items,
	get_stock_map,
	get_item_map,
	get_reserved_in_stock_warehouse_map,
	compute_so_qualification_flags,
)
from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
	get_line_reserved_map,
	_get_so_date_map,
)

# How many rows each focus list returns. The lists are meant to be scanned and
# acted on, not paged through - the payload also carries the full count so the UI
# can say "showing 15 of 42".
BLOCKING_ITEMS_LIMIT = 15
OVERDUE_SOS_LIMIT = 25

# Qty below which a shortfall/requirement is floating-point noise rather than a
# real one. Same 0.0001 tolerance compute_so_qualification_flags uses, so "needs
# production" means the same thing everywhere - and so the focus list, its "showing
# X of N" count, and the Production Required card can never disagree by a crumb.
QTY_TOLERANCE = 0.0001

# Ageing buckets for the dispatch-risk split, in days from today against the
# report's active Date Basis. Overdue is reported separately (negative days).
AGEING_BUCKETS = (
	("due_7", 7),
	("due_30", 30),
)


@frappe.whitelist()
def get_dashboard_metrics(filters=None):
	"""Everything the Dashboard tab draws, for the given FGSRM filters.

	Returns a JSON-serialisable dict:
	  {currency, as_on, date_basis, cards[], ageing[], blocking_items[],
	   overdue_sos[], counts{}}
	Read-only - it creates nothing and changes nothing."""
	if not frappe.has_permission("Sales Order", "read"):
		frappe.throw(
			_("You are not permitted to read Sales Orders."), frappe.PermissionError
		)

	filters = frappe.parse_json(filters) if filters else {}
	# Display-only toggles must not narrow the dashboard (see module docstring).
	filters = {
		k: v
		for k, v in filters.items()
		if k not in ("only_unreserved", "group_by_so", "view_mode")
	}

	so_items = get_open_so_items(filters)
	if not so_items:
		return _empty_payload(filters)

	fg_items = sorted(set(r.item_code for r in so_items))
	sos = sorted(set(r.sales_order for r in so_items))

	line_reserved = get_line_reserved_map([r.so_item for r in so_items])
	stock_map = get_stock_map(fg_items)
	item_map = get_item_map(fg_items)
	item_free_stock_map = _item_free_stock_map(fg_items, sos, stock_map, filters)

	# ready / coverable per SO - the same helper (and therefore the same
	# definition) behind the report's view buttons and custom_material_status.
	so_ok = compute_so_qualification_flags(so_items, line_reserved, item_free_stock_map)
	so_date_map = _get_so_date_map(sos, filters.get("date_basis"))

	lines = _build_lines(so_items, line_reserved)
	so_roll = _rollup_by_so(lines, so_ok, so_date_map)
	item_roll = _rollup_by_item(lines, so_ok, item_free_stock_map, item_map)

	return {
		"currency": _company_currency(),
		"as_on": str(getdate(today())),
		"date_basis": filters.get("date_basis") or "Document Creation Date",
		"cards": _build_cards(lines, so_roll, stock_map, item_roll, filters),
		"ageing": _build_ageing(so_roll),
		"blocking_items": _blocking_items(item_roll),
		"overdue_sos": _overdue_sos(so_roll),
		"counts": {
			"sales_orders": len(sos),
			"items": len(fg_items),
			"blocking_items_total": sum(
				1 for i in item_roll.values() if i["to_produce"] > QTY_TOLERANCE
			),
			"overdue_sos_total": sum(1 for s in so_roll.values() if s["days_overdue"] > 0),
		},
	}


# --------------------------------------------------------------------------- #
# Building blocks
# --------------------------------------------------------------------------- #


def _company_currency():
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	return (company and frappe.db.get_value("Company", company, "default_currency")) or ""


def _empty_payload(filters):
	"""Same shape as a populated payload, all zeroes - so the client renders an
	empty dashboard rather than special-casing "no data"."""
	return {
		"currency": _company_currency(),
		"as_on": str(getdate(today())),
		"date_basis": filters.get("date_basis") or "Document Creation Date",
		"cards": _build_cards([], {}, {}, {}, filters),
		"ageing": _build_ageing({}),
		"blocking_items": [],
		"overdue_sos": [],
		"counts": {
			"sales_orders": 0,
			"items": 0,
			"blocking_items_total": 0,
			"overdue_sos_total": 0,
		},
	}


def _item_free_stock_map(fg_items, sos, stock_map, filters):
	"""Per-item free stock in STOCK_WAREHOUSE on the report's Unreserved Stock
	Basis - identical to the report's own item_free_stock_map so the dashboard and
	the table can never disagree about what's coverable."""
	unreserved_basis = filters.get("unreserved_basis") or "All Reservations"
	displayed_reserved = (
		get_reserved_in_stock_warehouse_map(sos)
		if unreserved_basis == "Only Displayed SOs"
		else {}
	)
	out = {}
	for item in fg_items:
		stock = stock_map.get(item) or frappe._dict()
		if unreserved_basis == "Only Displayed SOs":
			reserved_from_stock = flt(displayed_reserved.get(item, 0.0))
		else:
			reserved_from_stock = flt(stock.get("reserved_qty"))
		out[item] = flt(stock.get("actual_qty")) - reserved_from_stock
	return out


def _build_lines(so_items, line_reserved):
	"""One enriched record per open SO line: pending / reserved / short in both
	qty and ex-tax sale value. `net_rate` (base_net_rate) is the ex-tax company
	-currency rate; it falls back to `rate` on the rare line where net_rate is
	unset (e.g. a legacy row), so value is never silently zero."""
	lines = []
	for r in so_items:
		res = line_reserved.get(r.so_item) or frappe._dict()
		reserved = flt(res.get("reserved_qty"))
		pending = flt(r.pending_qty)
		short = max(0.0, pending - reserved)
		rate = flt(r.get("net_rate")) or flt(r.get("rate"))

		lines.append(
			{
				"sales_order": r.sales_order,
				"customer": r.customer,
				"item_code": r.item_code,
				"pending_qty": pending,
				"reserved_qty": reserved,
				"short_qty": short,
				"rate": rate,
				"pending_value": pending * rate,
				"reserved_value": reserved * rate,
				"short_value": short * rate,
			}
		)
	return lines


def _rollup_by_so(lines, so_ok, so_date_map):
	"""Per Sales Order: qty/value totals, its fulfilment bucket, and how overdue
	it is against the active Date Basis.

	Bucket is exactly one of - mirroring the report's view definitions:
	  ready  -> every line fully reserved (nothing left to reserve; dispatchable)
	  cover  -> not ready, but every line's shortfall is coverable by free stock
	  produce-> at least one shortfall stock can't cover (needs manufacturing)"""
	as_on = getdate(today())
	out = {}
	for ln in lines:
		so = ln["sales_order"]
		agg = out.get(so)
		if agg is None:
			flags = so_ok.get(so) or {"ready": False, "coverable": False}
			so_date = so_date_map.get(so)
			days_overdue = (as_on - getdate(so_date)).days if so_date else 0
			agg = {
				"sales_order": so,
				"customer": ln["customer"],
				"so_date": str(so_date) if so_date else None,
				# Positive = overdue by that many days; <= 0 = not yet due.
				"days_overdue": days_overdue,
				"bucket": "ready" if flags["ready"] else ("cover" if flags["coverable"] else "produce"),
				"pending_qty": 0.0,
				"pending_value": 0.0,
				"reserved_qty": 0.0,
				"short_qty": 0.0,
				"short_value": 0.0,
			}
			out[so] = agg
		agg["pending_qty"] += ln["pending_qty"]
		agg["pending_value"] += ln["pending_value"]
		agg["reserved_qty"] += ln["reserved_qty"]
		agg["short_qty"] += ln["short_qty"]
		agg["short_value"] += ln["short_value"]
	return out


def _rollup_by_item(lines, so_ok, item_free_stock_map, item_map):
	"""Per FG item: the shortfall it represents, how much of that stock can't
	cover (to_produce), and how many Sales Orders it is holding up.

	`blocked_sos` counts DISTINCT Sales Orders that have a shortfall on this item
	AND are not already dispatchable - i.e. orders this item is genuinely holding
	up. That, alongside value at risk, is what ranks the Top Blocking Items list:
	an item short by a little but blocking eight orders matters more than its qty
	suggests.

	to_produce is recomputed here (Σ short − free stock, floored at 0) rather than
	read off the per-line column, because free stock is SHARED across an item's SO
	lines - summing the per-line figure would subtract the same free stock more
	than once. Same netting as _suggested_prodn_by_item."""
	out = {}
	for ln in lines:
		item = ln["item_code"]
		agg = out.get(item)
		if agg is None:
			details = item_map.get(item) or frappe._dict()
			agg = {
				"item_code": item,
				"item_name": details.get("item_name"),
				"free_stock": flt(item_free_stock_map.get(item, 0.0)),
				"short_qty": 0.0,
				"short_value": 0.0,
				"pending_qty": 0.0,
				"pending_value": 0.0,
				"_blocked": set(),
				"_rate_num": 0.0,
				"_rate_den": 0.0,
			}
			out[item] = agg
		agg["short_qty"] += ln["short_qty"]
		agg["short_value"] += ln["short_value"]
		agg["pending_qty"] += ln["pending_qty"]
		agg["pending_value"] += ln["pending_value"]
		# Weighted average ex-tax rate, for valuing this item's production.
		agg["_rate_num"] += ln["pending_value"]
		agg["_rate_den"] += ln["pending_qty"]

		if ln["short_qty"] > QTY_TOLERANCE:
			flags = so_ok.get(ln["sales_order"]) or {"ready": False}
			if not flags["ready"]:
				agg["_blocked"].add(ln["sales_order"])

	for agg in out.values():
		agg["blocked_sos"] = len(agg.pop("_blocked"))
		den = agg.pop("_rate_den")
		num = agg.pop("_rate_num")
		agg["avg_rate"] = (num / den) if den else 0.0
		agg["to_produce"] = max(0.0, agg["short_qty"] - agg["free_stock"])
		# Value of the production this item still needs, at its ex-tax sale rate.
		agg["to_produce_value"] = agg["to_produce"] * agg["avg_rate"]
	return out


# --------------------------------------------------------------------------- #
# Cards / ageing
# --------------------------------------------------------------------------- #


def _bucket_totals(so_roll, bucket):
	rows = [s for s in so_roll.values() if s["bucket"] == bucket]
	return {
		"value": sum(s["pending_value"] for s in rows),
		"qty": sum(s["pending_qty"] for s in rows),
		"count": len(rows),
	}


def _build_cards(lines, so_roll, stock_map, item_roll, filters):
	"""The number cards, in reading order: the order book, then how it splits by
	fulfilment state, then the risk/production numbers.

	Each card carries an `action` the client turns into a click-through (setting
	the report's view_mode / filters and switching back to the table tab), so a
	number is never a dead end."""
	total_value = sum(ln["pending_value"] for ln in lines)
	total_qty = sum(ln["pending_qty"] for ln in lines)

	ready = _bucket_totals(so_roll, "ready")
	cover = _bucket_totals(so_roll, "cover")
	produce = _bucket_totals(so_roll, "produce")

	overdue_rows = [s for s in so_roll.values() if s["days_overdue"] > 0]
	overdue_value = sum(s["pending_value"] for s in overdue_rows)
	overdue_qty = sum(s["pending_qty"] for s in overdue_rows)

	# Capital physically locked up in reservations, at stock valuation rate.
	reserved_qty = sum(ln["reserved_qty"] for ln in lines)
	reserved_cogs = sum(
		ln["reserved_qty"] * flt((stock_map.get(ln["item_code"]) or {}).get("valuation_rate"))
		for ln in lines
	)

	# Production requirement, itemwise-netted (never a naive per-line sum).
	to_produce_qty = sum(i["to_produce"] for i in item_roll.values())
	to_produce_value = sum(i["to_produce_value"] for i in item_roll.values())
	to_produce_cogs = sum(
		i["to_produce"] * flt((stock_map.get(i["item_code"]) or {}).get("valuation_rate"))
		for i in item_roll.values()
	)

	return [
		{
			"key": "order_book",
			"label": _("Open Order Book"),
			"value": total_value,
			"qty": total_qty,
			"count": len(so_roll),
			"indicator": "blue",
			"hint": _("Total pending sale value (ex-tax) across every open Sales Order in view."),
			"action": {"type": "view", "view_mode": ""},
		},
		{
			"key": "ready",
			"label": _("Fully Reserved / Ready to Dispatch"),
			"value": ready["value"],
			"qty": ready["qty"],
			"count": ready["count"],
			"indicator": "green",
			"hint": _("Every line fully reserved - these orders can ship now."),
			"action": {"type": "view", "view_mode": "ready_to_dispatch"},
		},
		{
			"key": "coverable",
			"label": _("Coverable Now"),
			"value": cover["value"],
			"qty": cover["qty"],
			"count": cover["count"],
			"indicator": "orange",
			"hint": _("Not yet reserved, but free stock covers every shortfall - reserve to make dispatchable."),
			"action": {"type": "view", "view_mode": "possible_to_complete"},
		},
		{
			"key": "needs_production",
			"label": _("Needs Production"),
			"value": produce["value"],
			"qty": produce["qty"],
			"count": produce["count"],
			"indicator": "red",
			"hint": _("At least one shortfall stock can't cover - blocked until manufactured."),
			"action": {"type": "view", "view_mode": ""},
		},
		{
			"key": "overdue",
			"label": _("Overdue"),
			"value": overdue_value,
			"qty": overdue_qty,
			"count": len(overdue_rows),
			"indicator": "red",
			"hint": _("Past its date on the active Date Basis ({0}).").format(
				filters.get("date_basis") or _("Document Creation Date")
			),
			"action": None,
		},
		{
			"key": "to_produce",
			"label": _("Production Required"),
			"value": to_produce_value,
			"qty": to_produce_qty,
			"count": sum(1 for i in item_roll.values() if i["to_produce"] > QTY_TOLERANCE),
			"secondary": {"label": _("at COGS"), "value": to_produce_cogs},
			"indicator": "orange",
			"hint": _("Shortfall free stock can't cover, netted per item. Value = revenue it unlocks (ex-tax); COGS = cost to make."),
			"action": None,
		},
		{
			"key": "reserved_locked",
			"label": _("Reserved Stock Value"),
			"value": reserved_cogs,
			"qty": reserved_qty,
			"count": None,
			"indicator": "grey",
			"hint": _("Inventory value committed to reservations in {0}, at stock valuation rate.").format(
				STOCK_WAREHOUSE
			),
			"action": None,
		},
	]


def _build_ageing(so_roll):
	"""Order-book value split by how close it is to its date on the active Date
	Basis: already overdue, then the forward buckets, then everything beyond."""
	buckets = [
		{"key": "overdue", "label": _("Overdue"), "value": 0.0, "qty": 0.0, "count": 0, "indicator": "red"},
		{"key": "due_7", "label": _("Due in 7 days"), "value": 0.0, "qty": 0.0, "count": 0, "indicator": "orange"},
		{"key": "due_30", "label": _("Due in 8-30 days"), "value": 0.0, "qty": 0.0, "count": 0, "indicator": "blue"},
		{"key": "later", "label": _("Beyond 30 days"), "value": 0.0, "qty": 0.0, "count": 0, "indicator": "grey"},
	]
	index = {b["key"]: b for b in buckets}

	for s in so_roll.values():
		days_overdue = s["days_overdue"]
		if not s["so_date"]:
			# No date on the active Date Basis (the custom delivery-date field can
			# be unset). days_overdue is 0 for these, which would otherwise park
			# them in "Due in 7 days" and overstate near-term pressure - park them
			# at the far end instead. They're never counted as overdue.
			key = "later"
		elif days_overdue > 0:
			key = "overdue"
		else:
			# days_until = how many days from today until it's due.
			days_until = -days_overdue
			key = "later"
			for bucket_key, limit in AGEING_BUCKETS:
				if days_until <= limit:
					key = bucket_key
					break
		target = index[key]
		target["value"] += s["pending_value"]
		target["qty"] += s["pending_qty"]
		target["count"] += 1

	return buckets


# --------------------------------------------------------------------------- #
# Focus lists
# --------------------------------------------------------------------------- #


def _blocking_items(item_roll):
	"""Top Blocking Items - the FG items holding up dispatch.

	Only items that actually need manufacturing (to_produce > 0) qualify: an item
	whose shortfall free stock can already cover isn't blocking anything, it just
	needs reserving. Ranked by value at risk, with the count of Sales Orders it
	holds up as the tie-breaker and second signal."""
	rows = [i for i in item_roll.values() if i["to_produce"] > QTY_TOLERANCE]
	rows.sort(key=lambda i: (i["short_value"], i["blocked_sos"]), reverse=True)

	return [
		{
			"item_code": i["item_code"],
			"item_name": i["item_name"],
			"blocked_sos": i["blocked_sos"],
			"short_qty": flt(i["short_qty"], 2),
			"free_stock": flt(i["free_stock"], 2),
			"to_produce": flt(i["to_produce"], 2),
			"value_at_risk": flt(i["short_value"], 2),
		}
		for i in rows[:BLOCKING_ITEMS_LIMIT]
	]


def _overdue_sos(so_roll):
	"""Overdue Sales Orders - past their date on the active Date Basis, most
	overdue first. Carries Material Status where that custom field exists, and the
	fulfilment bucket so the UI can show whether it's merely unreserved (fixable
	right now) or genuinely waiting on production."""
	rows = [s for s in so_roll.values() if s["days_overdue"] > 0]
	rows.sort(key=lambda s: (s["days_overdue"], s["pending_value"]), reverse=True)
	rows = rows[:OVERDUE_SOS_LIMIT]
	if not rows:
		return []

	shown = [s["sales_order"] for s in rows]
	status_map = _material_status_map(shown)
	customer_names = _customer_name_map([s["customer"] for s in rows])

	return [
		{
			"sales_order": s["sales_order"],
			"customer": s["customer"],
			"customer_name": customer_names.get(s["customer"]) or s["customer"],
			"so_date": s["so_date"],
			"days_overdue": s["days_overdue"],
			"bucket": s["bucket"],
			"material_status": status_map.get(s["sales_order"]),
			"pending_qty": flt(s["pending_qty"], 2),
			"pending_value": flt(s["pending_value"], 2),
			"short_value": flt(s["short_value"], 2),
		}
		for s in rows
	]


def _material_status_map(sos):
	"""{sales_order: custom_material_status}, guarded so this still runs on a site
	where the Material Status custom field hasn't been installed."""
	if not sos or not frappe.db.has_column("Sales Order", "custom_material_status"):
		return {}
	return {
		r.name: r.custom_material_status
		for r in frappe.get_all(
			"Sales Order",
			filters={"name": ["in", sos]},
			fields=["name", "custom_material_status"],
		)
	}


def _customer_name_map(customers):
	names = sorted({c for c in customers if c})
	if not names:
		return {}
	return {
		r.name: r.customer_name
		for r in frappe.get_all(
			"Customer", filters={"name": ["in", names]}, fields=["name", "customer_name"]
		)
	}
