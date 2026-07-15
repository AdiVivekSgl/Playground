# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Liability & Cash Flow Forecast
=======================================

Forward-looking view of "how much cash we expect to pay suppliers, and roughly
when", consolidating three non-overlapping liability stages so a rupee is only
ever counted at its most-advanced stage:

  Stage 1  Actual              - submitted Purchase Invoice outstanding, taken
                                 per Payment Schedule milestone (real due dates).
  Stage 2  Received / Unbilled - submitted Purchase Receipt value not yet
                                 invoiced (base_amount - billed_amt).
  Stage 3  Future Commitment   - open Purchase Order value neither received nor
                                 billed = base_amount - max(received, billed).

Double-counting is avoided using ERPNext's own received/billed amounts rather
than summing PO + PR + PI totals (see the module comments on each stage). Worked
example from the brief (PO 10L, received 6L, invoiced 4L, paid 1L):
  Stage1 = 3L (PI outstanding), Stage2 = 2L (received-unbilled),
  Stage3 = 10 - max(6,4) = 4L  ->  total exposure 9L, not 10L + ...

Dates: Stage 1 uses the invoice's actual Payment Schedule due dates (ACTUAL).
Stages 2 & 3 are FORECAST - base date (PR posting date / PO item expected
delivery date) plus the credit days of the applicable Payment Terms Template
(PO template -> Supplier default -> none = due on base date). Each milestone of
a multi-term template becomes its own forecast row.

Amounts are in COMPANY CURRENCY. Stage 2/3 item net amounts are grossed up by the
document's grand-total / net-total ratio so they are broadly tax-inclusive like
Stage 1.

Assumptions / simplifications to verify on-site (ERPNext internals, not testable
here):
  - Return documents (is_return = 1: Purchase Returns, Debit Notes) are EXCLUDED
    from the positive stages; PI outstanding already reflects reconciled debit
    notes.
  - billed_amt (PR/PO item) is treated as company currency; PI outstanding is
    converted to base via conversion_rate.
  - Tax gross-up is a proportional approximation, not a per-line tax computation.
  - Supplier advances are not separately modelled (they reduce PI outstanding
    once allocated).
Architecture note: a fourth upstream "Approved Procurement" stage can be added
later as another _get_*_rows() generator feeding the same pipeline.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

STAGE_ACTUAL = "Actual"
STAGE_UNBILLED = "Received / Unbilled"
STAGE_FUTURE = "Future Commitment"

# Hard cutoff: liabilities with a forecast payment date before this date are
# always hidden, regardless of the Include Overdue / From Date filters. Change
# here if the reporting horizon moves.
HARD_MIN_DATE = "2026-04-01"


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.get("company"):
		frappe.throw(_("Please select a Company."))

	rows = []
	stage = filters.get("liability_stage")
	# Stage 1 (Actual) is invoice/payment-schedule level, not item-attributable -
	# so it's excluded when an item-level filter is active, to keep the item view
	# honest rather than showing unrelated invoice liabilities.
	item_filter = any(filters.get(f) for f in ("item_code", "item_group", "cost_center", "project"))
	if stage in (None, "", STAGE_ACTUAL) and not item_filter:
		rows += _get_actual_rows(filters)
	if stage in (None, "", STAGE_UNBILLED):
		rows += _get_unbilled_rows(filters)
	if stage in (None, "", STAGE_FUTURE):
		rows += _get_future_rows(filters)

	rows = _finalize_rows(rows, filters)
	rows.sort(key=lambda r: (getdate(r["forecast_payment_date"]) if r.get("forecast_payment_date") else getdate("2100-01-01")))

	return get_columns(), rows, None, _get_chart(rows), _get_report_summary(rows)


# --------------------------------------------------------------------------- #
# Columns
# --------------------------------------------------------------------------- #

def get_columns():
	return [
		{"label": _("Forecast Payment Date"), "fieldname": "forecast_payment_date", "fieldtype": "Date", "width": 130},
		{"label": _("Days to Due"), "fieldname": "days_to_due", "fieldtype": "Int", "width": 90},
		{"label": _("Bucket"), "fieldname": "date_bucket", "fieldtype": "Data", "width": 100},
		{"label": _("Due Status"), "fieldname": "due_status", "fieldtype": "Data", "width": 90},
		{"label": _("Liability Stage"), "fieldname": "liability_stage", "fieldtype": "Data", "width": 140},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 130},
		{"label": _("Supplier Name"), "fieldname": "supplier_name", "fieldtype": "Data", "width": 160},
		{"label": _("Source Type"), "fieldname": "source_doctype", "fieldtype": "Data", "width": 120},
		{"label": _("Source Document"), "fieldname": "source_document", "fieldtype": "Dynamic Link", "options": "source_doctype", "width": 150},
		{"label": _("Purchase Order"), "fieldname": "purchase_order", "fieldtype": "Link", "options": "Purchase Order", "width": 130},
		{"label": _("Purchase Receipt"), "fieldname": "purchase_receipt", "fieldtype": "Link", "options": "Purchase Receipt", "width": 130},
		{"label": _("Purchase Invoice"), "fieldname": "purchase_invoice", "fieldtype": "Link", "options": "Purchase Invoice", "width": 130},
		{"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 120},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 150},
		{"label": _("Expected Delivery Date"), "fieldname": "expected_delivery_date", "fieldtype": "Date", "width": 130},
		{"label": _("Receipt Date"), "fieldname": "receipt_date", "fieldtype": "Date", "width": 110},
		{"label": _("Gross / Base Amount"), "fieldname": "gross_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Paid / Adjusted"), "fieldname": "paid_adjusted", "fieldtype": "Currency", "width": 120},
		{"label": _("Forecast Liability"), "fieldname": "forecast_liability", "fieldtype": "Currency", "width": 140},
		{"label": _("Payment Term"), "fieldname": "payment_term", "fieldtype": "Data", "width": 130},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 70},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 220},
	]


# --------------------------------------------------------------------------- #
# Stage 1 - Actual (Purchase Invoice outstanding, per Payment Schedule)
# --------------------------------------------------------------------------- #

def _get_actual_rows(filters):
	conds, values = _doc_conditions(filters, "pi", supplier_field="supplier")
	invoices = frappe.db.sql(
		"""
		SELECT pi.name, pi.supplier, pi.supplier_name, pi.posting_date, pi.due_date,
			pi.outstanding_amount, pi.grand_total, pi.base_grand_total, pi.currency,
			pi.conversion_rate
		FROM `tabPurchase Invoice` pi
		WHERE pi.docstatus = 1 AND pi.is_return = 0
			AND pi.outstanding_amount > 0
			AND pi.company = %(company)s
			{conds}
		""".format(conds=conds),
		values,
		as_dict=True,
	)
	if not invoices:
		return []

	names = [i.name for i in invoices]
	schedule = frappe.db.sql(
		"""
		SELECT parent, due_date, payment_amount, paid_amount, outstanding, payment_term, description
		FROM `tabPayment Schedule`
		WHERE parenttype = 'Purchase Invoice' AND parent IN %(names)s
		ORDER BY idx
		""",
		{"names": names},
		as_dict=True,
	)
	sched_by_pi = {}
	for s in schedule:
		sched_by_pi.setdefault(s.parent, []).append(s)

	rows = []
	for pi in invoices:
		conv = flt(pi.conversion_rate) or 1.0
		sched = sched_by_pi.get(pi.name)
		if sched:
			# Distribute the invoice's ACTUAL outstanding_amount across the
			# milestones, earliest due first (payments settle the earliest terms
			# first). Trusting each row's own paid_amount/outstanding over-reports
			# a partially paid invoice: those fields are only maintained when
			# payments are allocated per payment term, which most sites don't do -
			# so a part-paid invoice would otherwise show its FULL value here.
			sched = sorted(
				sched, key=lambda s: getdate(s.due_date or pi.due_date or pi.posting_date)
			)
			total_sched = sum(flt(s.payment_amount) for s in sched)
			remaining_paid = max(0.0, total_sched - flt(pi.outstanding_amount))
			for s in sched:
				pa = flt(s.payment_amount)
				applied = min(pa, remaining_paid)  # portion of this milestone already paid
				remaining_paid -= applied
				out = pa - applied
				if out <= 0.0001:
					continue
				rows.append(_row(
					stage=STAGE_ACTUAL, source_doctype="Purchase Invoice", source_document=pi.name,
					supplier=pi.supplier, supplier_name=pi.supplier_name, purchase_invoice=pi.name,
					forecast_date=s.due_date or pi.due_date or pi.posting_date,
					gross=pa * conv, paid=applied * conv,
					forecast=out * conv, currency=pi.currency,
					payment_term=s.payment_term or s.description or _("Invoice milestone"),
					remarks=_("Actual due date (Payment Schedule)"),
				))
		else:
			rows.append(_row(
				stage=STAGE_ACTUAL, source_doctype="Purchase Invoice", source_document=pi.name,
				supplier=pi.supplier, supplier_name=pi.supplier_name, purchase_invoice=pi.name,
				forecast_date=pi.due_date or pi.posting_date,
				gross=flt(pi.base_grand_total), paid=flt(pi.base_grand_total) - flt(pi.outstanding_amount) * conv,
				forecast=flt(pi.outstanding_amount) * conv, currency=pi.currency,
				payment_term=_("Invoice due date"), remarks=_("Actual due date (invoice)"),
			))
	return rows


# --------------------------------------------------------------------------- #
# Stage 2 - Received / Unbilled (Purchase Receipt item, not yet invoiced)
# --------------------------------------------------------------------------- #

def _get_unbilled_rows(filters):
	conds, values = _doc_conditions(filters, "pr", supplier_field="supplier", item_alias="pri")
	items = frappe.db.sql(
		"""
		SELECT pr.name AS pr_name, pr.supplier, pr.supplier_name, pr.posting_date,
			pr.currency, pr.base_grand_total, pr.base_net_total,
			pri.item_code, pri.item_name, pri.base_amount, pri.billed_amt,
			pri.purchase_order, pri.cost_center, pri.project
		FROM `tabPurchase Receipt Item` pri
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
		WHERE pr.docstatus = 1 AND pr.is_return = 0
			AND pr.company = %(company)s
			AND (pri.base_amount - IFNULL(pri.billed_amt, 0)) > 0.0001
			{conds}
		""".format(conds=conds),
		values,
		as_dict=True,
	)
	if not items:
		return []

	po_terms = _po_terms_map({r.purchase_order for r in items if r.purchase_order})
	supplier_terms = _supplier_terms_map({r.supplier for r in items})

	rows = []
	for r in items:
		tax_mult = _tax_mult(r.base_grand_total, r.base_net_total)
		unbilled = max(0.0, flt(r.base_amount) - flt(r.billed_amt)) * tax_mult
		if unbilled <= 0.0001:
			continue
		billed = flt(r.billed_amt) * tax_mult
		template = po_terms.get(r.purchase_order) or supplier_terms.get(r.supplier)
		for portion, credit_days, term in _terms_milestones(template):
			amt = unbilled * portion
			if amt <= 0.0001:
				continue
			rows.append(_row(
				stage=STAGE_UNBILLED, source_doctype="Purchase Receipt", source_document=r.pr_name,
				supplier=r.supplier, supplier_name=r.supplier_name, purchase_receipt=r.pr_name,
				purchase_order=r.purchase_order, item_code=r.item_code, item_name=r.item_name,
				receipt_date=r.posting_date, forecast_date=add_days(r.posting_date, credit_days),
				gross=(flt(r.base_amount) * tax_mult) * portion, paid=billed * portion, forecast=amt,
				currency=r.currency, payment_term=term or _("On receipt"),
				remarks=_("Forecast: receipt date + {0}d{1}").format(credit_days, _(" ({0})").format(term) if term else ""),
			))
	return rows


# --------------------------------------------------------------------------- #
# Stage 3 - Future Commitment (Purchase Order pending, neither received nor billed)
# --------------------------------------------------------------------------- #

def _get_future_rows(filters):
	conds, values = _doc_conditions(filters, "po", supplier_field="supplier", item_alias="poi")
	items = frappe.db.sql(
		"""
		SELECT po.name AS po_name, po.supplier, po.supplier_name, po.transaction_date,
			po.schedule_date AS po_schedule_date, po.currency, po.base_grand_total,
			po.base_net_total, po.payment_terms_template,
			poi.item_code, poi.item_name, poi.qty, poi.received_qty, poi.base_rate,
			poi.base_amount, poi.billed_amt, poi.schedule_date, poi.cost_center, poi.project
		FROM `tabPurchase Order Item` poi
		INNER JOIN `tabPurchase Order` po ON po.name = poi.parent
		WHERE po.docstatus = 1 AND po.status NOT IN ('Closed', 'Cancelled')
			AND po.company = %(company)s
			{conds}
		""".format(conds=conds),
		values,
		as_dict=True,
	)
	if not items:
		return []

	supplier_terms = _supplier_terms_map({r.supplier for r in items})
	today = getdate(nowdate())

	rows = []
	for r in items:
		received_value = flt(r.received_qty) * flt(r.base_rate)
		advanced = max(received_value, flt(r.billed_amt))  # most-advanced already-committed value
		future_net = flt(r.base_amount) - advanced
		if future_net <= 0.0001:
			continue
		tax_mult = _tax_mult(r.base_grand_total, r.base_net_total)
		future = future_net * tax_mult
		gross = flt(r.base_amount) * tax_mult

		# Expected delivery per the PO (item row, else header). Shown as-is in the
		# Expected Delivery Date column.
		expected_delivery = r.schedule_date or r.po_schedule_date
		# For the payment FORECAST only, never assume goods arrive in the past: use
		# the greater of today or the expected delivery date as the base date.
		edd = expected_delivery or r.transaction_date
		base_date = max(today, getdate(edd)) if edd else today
		clamped = edd and getdate(edd) < today

		template = r.payment_terms_template or supplier_terms.get(r.supplier)
		for portion, credit_days, term in _terms_milestones(template):
			amt = future * portion
			if amt <= 0.0001:
				continue
			rows.append(_row(
				stage=STAGE_FUTURE, source_doctype="Purchase Order", source_document=r.po_name,
				supplier=r.supplier, supplier_name=r.supplier_name, purchase_order=r.po_name,
				item_code=r.item_code, item_name=r.item_name,
				expected_delivery_date=expected_delivery,
				forecast_date=add_days(base_date, credit_days),
				gross=gross * portion, paid=advanced * tax_mult * portion, forecast=amt,
				currency=r.currency, payment_term=term or _("On delivery"),
				remarks=_("Forecast: {0} + {1}d{2}").format(
					_("today (delivery overdue)") if clamped else _("expected delivery"),
					credit_days,
					_(" ({0})").format(term) if term else "",
				),
			))
	return rows


# --------------------------------------------------------------------------- #
# Row builder + finalisation (buckets, due status, date filtering)
# --------------------------------------------------------------------------- #

def _row(**kw):
	kw.setdefault("purchase_order", None)
	kw.setdefault("purchase_receipt", None)
	kw.setdefault("purchase_invoice", None)
	kw.setdefault("item_code", None)
	kw.setdefault("item_name", None)
	kw.setdefault("expected_delivery_date", None)
	kw.setdefault("receipt_date", None)
	return {
		"liability_stage": kw["stage"],
		"source_doctype": kw["source_doctype"],
		"source_document": kw["source_document"],
		"supplier": kw.get("supplier"),
		"supplier_name": kw.get("supplier_name"),
		"purchase_order": kw.get("purchase_order"),
		"purchase_receipt": kw.get("purchase_receipt"),
		"purchase_invoice": kw.get("purchase_invoice"),
		"item_code": kw.get("item_code"),
		"item_name": kw.get("item_name"),
		"expected_delivery_date": kw.get("expected_delivery_date"),
		"receipt_date": kw.get("receipt_date"),
		"forecast_payment_date": getdate(kw["forecast_date"]) if kw.get("forecast_date") else None,
		"gross_amount": flt(kw.get("gross")),
		"paid_adjusted": flt(kw.get("paid")),
		"forecast_liability": flt(kw.get("forecast")),
		"currency": kw.get("currency"),
		"payment_term": kw.get("payment_term"),
		"remarks": kw.get("remarks"),
	}


def _finalize_rows(rows, filters):
	today = getdate(nowdate())
	include_overdue = cint(filters.get("include_overdue", 1))
	from_date = getdate(filters.get("from_date")) if filters.get("from_date") else None
	to_date = getdate(filters.get("to_date")) if filters.get("to_date") else None

	hard_min = getdate(HARD_MIN_DATE)

	out = []
	for r in rows:
		d = r["forecast_payment_date"] or today
		# Hard cutoff - never show liabilities due before HARD_MIN_DATE.
		if d < hard_min:
			continue
		is_overdue = d < today
		if not include_overdue and is_overdue:
			continue
		if to_date and d > to_date:
			continue
		if from_date and d < from_date and not (include_overdue and is_overdue):
			continue

		days = (d - today).days
		r["days_to_due"] = days
		r["due_status"] = _("Overdue") if days < 0 else (_("Due") if days == 0 else _("Upcoming"))
		r["date_bucket"] = _bucket(days)
		out.append(r)
	return out


def _bucket(days):
	if days < 0:
		return _("Overdue")
	if days == 0:
		return _("Due Today")
	if days <= 7:
		return _("Next 7 Days")
	if days <= 15:
		return _("8-15 Days")
	if days <= 30:
		return _("16-30 Days")
	if days <= 60:
		return _("31-60 Days")
	if days <= 90:
		return _("61-90 Days")
	return _("90+ Days")


# --------------------------------------------------------------------------- #
# Payment terms helpers
# --------------------------------------------------------------------------- #

def _terms_milestones(template):
	"""[(portion_fraction, credit_days, label)] for a Payment Terms Template.
	Falls back to a single "due on base date" milestone when there's no template
	or no detail rows."""
	if not template:
		return [(1.0, 0, None)]
	rows = frappe.get_all(
		"Payment Terms Template Detail",
		filters={"parent": template},
		fields=["invoice_portion", "credit_days", "description", "payment_term"],
		order_by="idx",
	)
	out = []
	for r in rows:
		portion = flt(r.invoice_portion) / 100.0
		if portion <= 0:
			continue
		out.append((portion, cint(r.credit_days), r.payment_term or r.description))
	return out or [(1.0, 0, None)]


def _po_terms_map(po_names):
	po_names = [p for p in po_names if p]
	if not po_names:
		return {}
	rows = frappe.get_all(
		"Purchase Order", filters={"name": ["in", po_names]},
		fields=["name", "payment_terms_template"],
	)
	return {r.name: r.payment_terms_template for r in rows if r.payment_terms_template}


def _supplier_terms_map(suppliers):
	suppliers = [s for s in suppliers if s]
	if not suppliers:
		return {}
	rows = frappe.get_all(
		"Supplier", filters={"name": ["in", suppliers]}, fields=["name", "payment_terms"],
	)
	return {r.name: r.payment_terms for r in rows if r.payment_terms}


def _tax_mult(base_grand_total, base_net_total):
	"""Grand-total / net-total ratio, to make item net amounts broadly
	tax-inclusive. Guarded to 1.0 when net total is unavailable."""
	net = flt(base_net_total)
	if net <= 0:
		return 1.0
	return flt(base_grand_total) / net


# --------------------------------------------------------------------------- #
# Filter conditions
# --------------------------------------------------------------------------- #

def _doc_conditions(filters, alias, supplier_field="supplier", item_alias=None):
	"""Build the shared WHERE fragment + params for a stage's query. `alias` is
	the header table alias (pi/pr/po); `item_alias` the item table alias where
	item/cost-centre/project filters apply."""
	conds = []
	values = {"company": filters.get("company")}

	if filters.get("supplier"):
		conds.append("{0}.supplier = %(supplier)s".format(alias))
		values["supplier"] = filters.get("supplier")
	if filters.get("supplier_group"):
		conds.append(
			"{0}.supplier IN (SELECT name FROM `tabSupplier` WHERE supplier_group = %(supplier_group)s)".format(alias)
		)
		values["supplier_group"] = filters.get("supplier_group")

	# Document-reference filters (only bite on the relevant stage).
	if filters.get("purchase_invoice") and alias == "pi":
		conds.append("pi.name = %(purchase_invoice)s")
		values["purchase_invoice"] = filters.get("purchase_invoice")
	if filters.get("purchase_receipt") and alias == "pr":
		conds.append("pr.name = %(purchase_receipt)s")
		values["purchase_receipt"] = filters.get("purchase_receipt")
	if filters.get("purchase_order"):
		if alias == "po":
			conds.append("po.name = %(purchase_order)s")
			values["purchase_order"] = filters.get("purchase_order")
		elif item_alias:
			conds.append("{0}.purchase_order = %(purchase_order)s".format(item_alias))
			values["purchase_order"] = filters.get("purchase_order")

	if item_alias:
		if filters.get("item_code"):
			conds.append("{0}.item_code = %(item_code)s".format(item_alias))
			values["item_code"] = filters.get("item_code")
		if filters.get("item_group"):
			conds.append(
				"{0}.item_code IN (SELECT name FROM `tabItem` WHERE item_group = %(item_group)s)".format(item_alias)
			)
			values["item_group"] = filters.get("item_group")
		if filters.get("cost_center"):
			conds.append("{0}.cost_center = %(cost_center)s".format(item_alias))
			values["cost_center"] = filters.get("cost_center")
		if filters.get("project"):
			conds.append("{0}.project = %(project)s".format(item_alias))
			values["project"] = filters.get("project")

	return (" AND " + " AND ".join(conds)) if conds else "", values


# --------------------------------------------------------------------------- #
# Summary + chart
# --------------------------------------------------------------------------- #

def _get_report_summary(rows):
	today = getdate(nowdate())

	def total(pred):
		return sum(flt(r["forecast_liability"]) for r in rows if pred(r))

	actual = total(lambda r: r["liability_stage"] == STAGE_ACTUAL)
	unbilled = total(lambda r: r["liability_stage"] == STAGE_UNBILLED)
	future = total(lambda r: r["liability_stage"] == STAGE_FUTURE)
	exposure = actual + unbilled + future

	def due_within(days):
		horizon = getdate(add_days(today, days))
		return total(lambda r: (r["forecast_payment_date"] or today) <= horizon)

	overdue = total(lambda r: (r["forecast_payment_date"] or today) < today)

	return [
		{"label": _("Total Actual Payables"), "value": actual, "datatype": "Currency", "indicator": "Red"},
		{"label": _("Total Received / Unbilled"), "value": unbilled, "datatype": "Currency", "indicator": "Blue"},
		{"label": _("Total Future Commitments"), "value": future, "datatype": "Currency", "indicator": "Grey"},
		{"label": _("Total Purchase Exposure"), "value": exposure, "datatype": "Currency", "indicator": "Purple"},
		{"label": _("Overdue"), "value": overdue, "datatype": "Currency", "indicator": "Red"},
		{"label": _("Due in 7 Days"), "value": due_within(7), "datatype": "Currency", "indicator": "Orange"},
		{"label": _("Due in 30 Days"), "value": due_within(30), "datatype": "Currency", "indicator": "Yellow"},
		{"label": _("Due in 60 Days"), "value": due_within(60), "datatype": "Currency", "indicator": "Green"},
		{"label": _("Due in 90 Days"), "value": due_within(90), "datatype": "Currency", "indicator": "Green"},
	]


_BUCKET_ORDER = [
	"Overdue", "Due Today", "Next 7 Days", "8-15 Days", "16-30 Days",
	"31-60 Days", "61-90 Days", "90+ Days",
]


def _get_chart(rows):
	"""Stacked bar: cash requirement per bucket, split by liability stage."""
	labels = [_(b) for b in _BUCKET_ORDER]
	stages = [(STAGE_ACTUAL, "Actual"), (STAGE_UNBILLED, "Received / Unbilled"), (STAGE_FUTURE, "Future Commitment")]
	datasets = []
	for stage_value, stage_label in stages:
		values = []
		for b in _BUCKET_ORDER:
			values.append(
				sum(flt(r["forecast_liability"]) for r in rows if r.get("date_bucket") == _(b) and r["liability_stage"] == stage_value)
			)
		datasets.append({"name": _(stage_label), "values": values})

	return {
		"data": {"labels": labels, "datasets": datasets},
		"type": "bar",
		"barOptions": {"stacked": True},
		"title": _("Cash Requirement by Period"),
	}
