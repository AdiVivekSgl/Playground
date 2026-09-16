# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Orders Pending Delivery
================================

Two things in one report:

1. **Line listing (draft POs)** - every ``Purchase Order Item`` line on a draft
   (``docstatus = 0``) Purchase Order, ordered by supplier / PO / line, with the
   line's **Scheduled Date** and the PO grand total shown once per PO (on the
   first line, as in the original Query Report this replaces).

2. **Pending-delivery summaries (draft + submitted)** - the value still awaiting
   delivery, ``(qty - received_qty) x base_rate`` in company currency, pivoted by
   delivery urgency into two tables rendered below the filters:
     * by **Item Group**
     * by **Supplier**
   Each bucketed on the line's Scheduled Date relative to the base date (today):
     * ``<= near_days`` (default 10) - **includes anything overdue**
     * ``near_days - mid_days`` (default 10-30)
     * ``> mid_days`` (default 30)

The listing is draft-only by design; the summaries deliberately span draft and
submitted so the pending-delivery exposure is complete. KPI cards give the
per-bucket grand totals and a stacked bar shows the Item Group split.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, escape_html, flt, fmt_money, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})

	near_days = cint(filters.get("near_days")) or 10
	mid_days = cint(filters.get("mid_days")) or 30
	if mid_days <= near_days:
		mid_days = near_days + 20
	base_date = getdate(filters.get("base_date") or nowdate())

	columns = get_columns()
	data = get_listing(filters)

	pending = get_pending_lines(filters)
	group_pivot, supplier_pivot, totals = bucket_pending(pending, base_date, near_days, mid_days)

	currency = _company_currency(filters)
	message = build_summary_html(filters, group_pivot, supplier_pivot, totals, near_days, mid_days, currency)
	chart = build_chart(group_pivot, near_days, mid_days)
	report_summary = build_report_summary(totals, near_days, mid_days)

	return columns, data, message, chart, report_summary


# --------------------------------------------------------------------------- #
# Scope
# --------------------------------------------------------------------------- #

def _scope(filters, params, item_join_alias="poi"):
	"""Optional company / supplier / item-group scope, shared by both queries.

	Returns ``(join, conditions)``; item-group needs a join to tabItem because
	Purchase Order Item is not relied on to carry item_group.
	"""
	join = ""
	conditions = ""
	if filters.get("company"):
		conditions += " AND po.company = %(company)s"
		params["company"] = filters.get("company")
	if filters.get("supplier"):
		conditions += " AND po.supplier = %(supplier)s"
		params["supplier"] = filters.get("supplier")
	if filters.get("item_group"):
		join += " INNER JOIN `tabItem` itg ON itg.name = {0}.item_code".format(item_join_alias)
		conditions += " AND itg.item_group = %(item_group)s"
		params["item_group"] = filters.get("item_group")
	return join, conditions


def _company_currency(filters):
	if filters.get("company"):
		return frappe.get_cached_value("Company", filters.get("company"), "default_currency")
	return frappe.defaults.get_global_default("currency")


# --------------------------------------------------------------------------- #
# 1. Listing (draft POs)
# --------------------------------------------------------------------------- #

def get_listing(filters):
	params = {}
	join, conditions = _scope(filters, params)

	rows = frappe.db.sql(
		"""
		SELECT
			po.name           AS purchase_order,
			po.supplier_name  AS supplier_name,
			poi.item_code     AS item_code,
			poi.description   AS description,
			poi.qty           AS qty,
			poi.rate          AS rate,
			poi.amount        AS amount,
			poi.schedule_date AS schedule_date,
			poi.idx           AS idx,
			po.grand_total    AS grand_total
		FROM `tabPurchase Order` po
		INNER JOIN `tabPurchase Order Item` poi ON poi.parent = po.name
		{join}
		WHERE po.docstatus = 0
			{conditions}
		ORDER BY po.supplier_name, po.name, poi.idx
		""".format(join=join, conditions=conditions),
		params,
		as_dict=True,
	)

	data = []
	for r in rows:
		data.append({
			"purchase_order": r.purchase_order,
			"supplier_name": r.supplier_name,
			"item_code": r.item_code,
			"description": r.description,
			"qty": flt(r.qty),
			"rate": flt(r.rate),
			"amount": flt(r.amount),
			"schedule_date": r.schedule_date,
			# Grand total once per PO, on its first line - as in the original.
			"po_grand_total": flt(r.grand_total) if cint(r.idx) == 1 else None,
		})
	return data


# --------------------------------------------------------------------------- #
# 2. Pending-delivery lines (draft + submitted)
# --------------------------------------------------------------------------- #

def get_pending_lines(filters):
	params = {}
	join, conditions = _scope(filters, params)

	return frappe.db.sql(
		"""
		SELECT
			po.supplier_name                              AS supplier_name,
			COALESCE(it.item_group, 'Not Set')            AS item_group,
			poi.schedule_date                             AS schedule_date,
			(poi.qty - poi.received_qty) * poi.base_rate  AS pending_value
		FROM `tabPurchase Order` po
		INNER JOIN `tabPurchase Order Item` poi ON poi.parent = po.name
		LEFT JOIN `tabItem` it ON it.name = poi.item_code
		{join}
		WHERE po.docstatus IN (0, 1)
			AND (poi.qty - poi.received_qty) > 0
			{conditions}
		""".format(join=join, conditions=conditions),
		params,
		as_dict=True,
	)


def bucket_pending(rows, base_date, near_days, mid_days):
	near_cutoff = add_days(base_date, near_days)
	mid_cutoff = add_days(base_date, mid_days)

	group_pivot = {}
	supplier_pivot = {}
	totals = {"near": 0.0, "mid": 0.0, "far": 0.0, "total": 0.0}

	for r in rows:
		value = flt(r.pending_value)
		if not value:
			continue

		sd = getdate(r.schedule_date) if r.schedule_date else None
		# Overdue and undated lines fold into the nearest bucket.
		if sd is None or sd <= near_cutoff:
			bucket = "near"
		elif sd <= mid_cutoff:
			bucket = "mid"
		else:
			bucket = "far"

		group = r.item_group or "Not Set"
		supplier = r.supplier_name or "Unknown"

		group_pivot.setdefault(group, {"near": 0.0, "mid": 0.0, "far": 0.0, "total": 0.0})
		supplier_pivot.setdefault(supplier, {"near": 0.0, "mid": 0.0, "far": 0.0, "total": 0.0})

		group_pivot[group][bucket] += value
		group_pivot[group]["total"] += value
		supplier_pivot[supplier][bucket] += value
		supplier_pivot[supplier]["total"] += value
		totals[bucket] += value
		totals["total"] += value

	return group_pivot, supplier_pivot, totals


# --------------------------------------------------------------------------- #
# Rendering: HTML summaries, chart, KPI cards
# --------------------------------------------------------------------------- #

def _bucket_labels(near_days, mid_days):
	# Plain ASCII so the same labels are safe in HTML, KPI cards and chart legend.
	return [
		_("Within {0}d (incl. overdue)").format(near_days),
		_("{0} to {1} days").format(near_days, mid_days),
		_("Over {0} days").format(mid_days),
		_("Total"),
	]


def _money(value, currency):
	return fmt_money(flt(value), currency=currency)


def _pivot_table_html(title, row_header, pivot, labels, currency):
	head = (
		"<tr><th style='text-align:left'>{rh}</th>"
		"<th style='text-align:right'>{l0}</th>"
		"<th style='text-align:right'>{l1}</th>"
		"<th style='text-align:right'>{l2}</th>"
		"<th style='text-align:right'>{l3}</th></tr>"
	).format(rh=escape_html(row_header), l0=labels[0], l1=labels[1], l2=labels[2], l3=labels[3])

	body = []
	# Biggest exposure first.
	for name in sorted(pivot, key=lambda k: pivot[k]["total"], reverse=True):
		v = pivot[name]
		body.append(
			"<tr><td style='text-align:left'>{name}</td>"
			"<td style='text-align:right'>{near}</td>"
			"<td style='text-align:right'>{mid}</td>"
			"<td style='text-align:right'>{far}</td>"
			"<td style='text-align:right'><b>{total}</b></td></tr>".format(
				name=escape_html(name),
				near=_money(v["near"], currency),
				mid=_money(v["mid"], currency),
				far=_money(v["far"], currency),
				total=_money(v["total"], currency),
			)
		)

	tot = {"near": 0.0, "mid": 0.0, "far": 0.0, "total": 0.0}
	for v in pivot.values():
		for k in tot:
			tot[k] += v[k]
	footer = (
		"<tr style='border-top:2px solid #ccc'><td style='text-align:left'><b>{lbl}</b></td>"
		"<td style='text-align:right'><b>{near}</b></td>"
		"<td style='text-align:right'><b>{mid}</b></td>"
		"<td style='text-align:right'><b>{far}</b></td>"
		"<td style='text-align:right'><b>{total}</b></td></tr>"
	).format(
		lbl=_("Total"),
		near=_money(tot["near"], currency), mid=_money(tot["mid"], currency),
		far=_money(tot["far"], currency), total=_money(tot["total"], currency),
	)

	return (
		"<div style='flex:1;min-width:340px'>"
		"<h5 style='margin:0 0 6px'>{title}</h5>"
		"<table class='table table-bordered' style='font-size:12px'>"
		"<thead>{head}</thead><tbody>{body}</tbody><tfoot>{footer}</tfoot>"
		"</table></div>"
	).format(title=escape_html(title), head=head, body="".join(body) or "", footer=footer)


def build_summary_html(filters, group_pivot, supplier_pivot, totals, near_days, mid_days, currency):
	if not totals["total"]:
		return "<div class='text-muted'>%s</div>" % _("No pending-delivery lines for the current filters.")

	labels = _bucket_labels(near_days, mid_days)
	group_html = _pivot_table_html(_("Pending Delivery Value by Item Group"), _("Item Group"), group_pivot, labels, currency)
	supplier_html = _pivot_table_html(_("Pending Delivery Value by Supplier"), _("Supplier"), supplier_pivot, labels, currency)

	note = _("Draft + submitted POs, value = (qty - received) x rate, company currency. Overdue and undated lines are counted in the first bucket.")
	return (
		"<div style='margin:8px 0'>"
		"<div style='display:flex;gap:24px;flex-wrap:wrap'>{g}{s}</div>"
		"<div class='text-muted' style='margin-top:4px;font-size:11px'>{note}</div>"
		"</div>"
	).format(g=group_html, s=supplier_html, note=escape_html(note))


def build_chart(group_pivot, near_days, mid_days):
	if not group_pivot:
		return None
	top = sorted(group_pivot, key=lambda k: group_pivot[k]["total"], reverse=True)[:12]
	labels = _bucket_labels(near_days, mid_days)
	return {
		"data": {
			"labels": top,
			"datasets": [
				{"name": labels[0], "values": [flt(group_pivot[g]["near"], 2) for g in top]},
				{"name": labels[1], "values": [flt(group_pivot[g]["mid"], 2) for g in top]},
				{"name": labels[2], "values": [flt(group_pivot[g]["far"], 2) for g in top]},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
		"title": _("Pending Delivery Value by Item Group"),
	}


def build_report_summary(totals, near_days, mid_days):
	labels = _bucket_labels(near_days, mid_days)
	return [
		{"label": _("Total Pending Delivery"), "value": totals["total"], "datatype": "Currency", "indicator": "Blue"},
		{"label": labels[0], "value": totals["near"], "datatype": "Currency", "indicator": "Red"},
		{"label": labels[1], "value": totals["mid"], "datatype": "Currency", "indicator": "Orange"},
		{"label": labels[2], "value": totals["far"], "datatype": "Currency", "indicator": "Green"},
	]


# --------------------------------------------------------------------------- #
# Columns (listing)
# --------------------------------------------------------------------------- #

def get_columns():
	return [
		{"label": _("Purchase Order"), "fieldname": "purchase_order", "fieldtype": "Link", "options": "Purchase Order", "width": 150},
		{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 180},
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 150},
		{"label": _("Item Desc"), "fieldname": "description", "fieldtype": "Data", "width": 300},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
		{"label": _("Rate"), "fieldname": "rate", "fieldtype": "Currency", "width": 120},
		{"label": _("Amount"), "fieldname": "amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Scheduled Date"), "fieldname": "schedule_date", "fieldtype": "Date", "width": 130},
		{"label": _("PO Grand Total"), "fieldname": "po_grand_total", "fieldtype": "Currency", "width": 140},
	]
