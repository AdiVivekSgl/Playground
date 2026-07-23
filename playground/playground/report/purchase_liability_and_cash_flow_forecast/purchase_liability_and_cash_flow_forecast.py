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
  - billed_amt (PR/PO item) is netted DIRECTLY against base_amount, both in COMPANY
    currency ("Amount (Company Currency)"). On this deployment billed_amt is stored in
    company currency; applying conversion_rate on top over-corrected and zeroed forex
    rows, so no conversion is applied. PI outstanding is stored in the party-account
    currency and converted to base via conversion_rate when that account isn't company
    currency.
  - Tax gross-up is a proportional approximation, not a per-line tax computation.
  - Supplier advances are not separately modelled (they reduce PI outstanding
    once allocated).
Architecture note: a fourth upstream "Approved Procurement" stage can be added
later as another _get_*_rows() generator feeding the same pipeline.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, fmt_money, getdate, nowdate

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

	# Summary cards and the stacked chart are split by liability stage; a consolidated
	# PO can span stages ("Mixed"), so compute both from the stage-split detail rows
	# before any grouping - keeping them accurate and never double-counting a total.
	chart = _get_chart(rows)
	summary = _get_report_summary(rows)

	if cint(filters.get("consolidated")):
		rows = _consolidate_by_po(rows, filters)

	rows.sort(key=lambda r: (getdate(r["forecast_payment_date"]) if r.get("forecast_payment_date") else getdate("2100-01-01")))

	# NB: the Forecast Liability total is rendered client-side as a persistent bar that
	# updates with the datatable's inline column filters (see the .js). No server-side
	# total row is appended, so it can never be mixed into filtering / chart / summary.
	return get_columns(), rows, None, chart, summary


# --------------------------------------------------------------------------- #
# Columns
# --------------------------------------------------------------------------- #

def get_columns():
	# Lead block (summary): Liability Stage, Forecast Payment Date, Due Status,
	# Supplier, Forecast Liability. Then Supplier Name and the detail block. The
	# lead Forecast Liability uses a distinct fieldname (forecast_liability_lead)
	# because Frappe keys data by fieldname - it mirrors forecast_liability. Days
	# to Due and Bucket were relocated into the detail block. All money columns
	# point at the per-row `currency` field (company currency) for correct symbols.
	return [
		{"label": _("Liability Stage"), "fieldname": "liability_stage", "fieldtype": "Data", "width": 150},
		{"label": _("Forecast Payment Date"), "fieldname": "forecast_payment_date", "fieldtype": "Date", "width": 140},
		{"label": _("Due Status"), "fieldname": "due_status", "fieldtype": "Data", "width": 90},
		{"label": _("Supplier"), "fieldname": "supplier", "fieldtype": "Link", "options": "Supplier", "width": 130},
		{"label": _("Forecast Liability"), "fieldname": "forecast_liability_lead", "fieldtype": "Currency", "options": "currency", "width": 140},
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
		{"label": _("Gross / Base Amount"), "fieldname": "gross_amount", "fieldtype": "Currency", "options": "currency", "width": 130},
		{"label": _("Paid / Adjusted"), "fieldname": "paid_adjusted", "fieldtype": "Currency", "options": "currency", "width": 120},
		{"label": _("Forecast Liability"), "fieldname": "forecast_liability", "fieldtype": "Currency", "options": "currency", "width": 140},
		{"label": _("Payment Term"), "fieldname": "payment_term", "fieldtype": "Data", "width": 130},
		{"label": _("Currency"), "fieldname": "currency", "fieldtype": "Data", "width": 70},
		{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 220},
		{"label": _("Days to Due"), "fieldname": "days_to_due", "fieldtype": "Int", "width": 90},
		{"label": _("Bucket"), "fieldname": "date_bucket", "fieldtype": "Data", "width": 100},
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
			pi.conversion_rate, pi.credit_to
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

	# outstanding_amount is stored in the party-account (Credit To) currency, and
	# Payment Schedule payment_amount in the transaction currency. To avoid forex
	# rounding / double-conversion we work in COMPANY currency using the stored
	# base_payment_amount, and convert outstanding only when the payable account
	# isn't already in company currency.
	company_currency = frappe.get_cached_value("Company", filters.get("company"), "default_currency")
	credit_accounts = list({i.credit_to for i in invoices if i.credit_to})
	acct_ccy = {}
	if credit_accounts:
		for a in frappe.get_all(
			"Account", filters={"name": ["in", credit_accounts]}, fields=["name", "account_currency"]
		):
			acct_ccy[a.name] = a.account_currency

	names = [i.name for i in invoices]
	schedule = frappe.db.sql(
		"""
		SELECT parent, due_date, payment_amount, base_payment_amount, payment_term, description
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
		# Base (company-currency) outstanding: already base when the Credit To
		# account is in company currency, else convert from account currency.
		ccy = acct_ccy.get(pi.credit_to)
		out_in_base = not (ccy and company_currency and ccy != company_currency)
		base_outstanding = flt(pi.outstanding_amount) if out_in_base else flt(pi.outstanding_amount) * conv

		sched = sched_by_pi.get(pi.name)
		if sched:
			# Distribute the invoice's ACTUAL (base) outstanding across the
			# milestones, earliest due first (payments settle earliest terms first).
			# Trusting each row's own paid/outstanding over-reports a partially paid
			# invoice - those are only maintained with per-term payment allocation.
			sched = sorted(sched, key=lambda s: getdate(s.due_date or pi.due_date or pi.posting_date))

			def _mbase(s):
				return flt(s.base_payment_amount) or (flt(s.payment_amount) * conv)

			total_base = sum(_mbase(s) for s in sched)
			remaining_paid = max(0.0, total_base - base_outstanding)
			for s in sched:
				pab = _mbase(s)
				applied = min(pab, remaining_paid)  # portion of this milestone already paid
				remaining_paid -= applied
				out = pab - applied
				if out <= 0.01:  # below currency precision - treat as settled
					continue
				rows.append(_row(
					stage=STAGE_ACTUAL, source_doctype="Purchase Invoice", source_document=pi.name,
					supplier=pi.supplier, supplier_name=pi.supplier_name, purchase_invoice=pi.name,
					forecast_date=s.due_date or pi.due_date or pi.posting_date,
					gross=pab, paid=applied, forecast=out, currency=pi.currency,
					payment_term=s.payment_term or s.description or _("Invoice milestone"),
					remarks=_("Actual due date (Payment Schedule)"),
				))
		else:
			rows.append(_row(
				stage=STAGE_ACTUAL, source_doctype="Purchase Invoice", source_document=pi.name,
				supplier=pi.supplier, supplier_name=pi.supplier_name, purchase_invoice=pi.name,
				forecast_date=pi.due_date or pi.posting_date,
				gross=flt(pi.base_grand_total), paid=flt(pi.base_grand_total) - base_outstanding,
				forecast=base_outstanding, currency=pi.currency,
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
			AND pr.status = 'To Bill'
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
		# base_amount and billed_amt are both COMPANY currency here - net directly.
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
		received_value = flt(r.received_qty) * flt(r.base_rate)  # base_rate is company ccy
		# received_value, billed_amt and base_amount are all COMPANY currency here.
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
		"gross_amount": flt(kw.get("gross"), 2),
		"paid_adjusted": flt(kw.get("paid"), 2),
		"forecast_liability": flt(kw.get("forecast"), 2),
		"forecast_liability_lead": flt(kw.get("forecast"), 2),
		"currency": kw.get("currency"),
		"payment_term": kw.get("payment_term"),
		"remarks": kw.get("remarks"),
	}


def _finalize_rows(rows, filters):
	today = getdate(nowdate())
	# Every amount in a row is company (base) currency - stamp the row currency with
	# the company default so the Currency columns render the right symbol regardless
	# of the source document's transaction currency.
	company_currency = frappe.get_cached_value("Company", filters.get("company"), "default_currency")
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
		r["currency"] = company_currency
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
# Consolidated view (group + total by Purchase Order)
# --------------------------------------------------------------------------- #

def _consolidate_by_po(rows, filters):
	"""Collapse detail rows into one summary row per Purchase Order, totalling the
	amounts. Rows with no PO (Stage 1 Actual, and any PR not raised from a PO) are
	grouped by their own source document instead, so nothing is dropped. The group's
	forecast date is the EARLIEST milestone in it (the soonest cash need)."""
	company_currency = frappe.get_cached_value("Company", filters.get("company"), "default_currency")
	today = getdate(nowdate())

	groups = {}
	for r in rows:
		key = r.get("purchase_order") or r.get("source_document")
		groups.setdefault(key, []).append(r)

	out = []
	for key, grp in groups.items():
		stages = {g["liability_stage"] for g in grp}
		stage = next(iter(stages)) if len(stages) == 1 else _("Mixed")
		has_po = bool(grp[0].get("purchase_order"))
		src_type = grp[0].get("source_doctype")

		dates = [g["forecast_payment_date"] for g in grp if g.get("forecast_payment_date")]
		fdate = min(dates) if dates else None
		days = (fdate - today).days if fdate else None

		forecast = sum(flt(g["forecast_liability"]) for g in grp)
		out.append({
			"liability_stage": stage,
			"source_doctype": "Purchase Order" if has_po else src_type,
			"source_document": key,
			"supplier": grp[0].get("supplier"),
			"supplier_name": grp[0].get("supplier_name"),
			"purchase_order": key if has_po else None,
			"purchase_receipt": None,
			"purchase_invoice": key if (not has_po and src_type == "Purchase Invoice") else None,
			"item_code": None,
			"item_name": None,
			"expected_delivery_date": None,
			"receipt_date": None,
			"forecast_payment_date": fdate,
			"gross_amount": flt(sum(flt(g["gross_amount"]) for g in grp), 2),
			"paid_adjusted": flt(sum(flt(g["paid_adjusted"]) for g in grp), 2),
			"forecast_liability": flt(forecast, 2),
			"forecast_liability_lead": flt(forecast, 2),
			"currency": company_currency,
			"payment_term": _("Multiple") if len(grp) > 1 else grp[0].get("payment_term"),
			"remarks": _("Consolidated: {0} line(s)").format(len(grp)),
			"days_to_due": days if days is not None else "",
			"due_status": (
				(_("Overdue") if days < 0 else (_("Due") if days == 0 else _("Upcoming")))
				if days is not None else ""
			),
			"date_bucket": _bucket(days) if days is not None else "",
		})
	return out


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

	# Number cards show a literal "Rs/-" prefix: pre-format the amount as text (Indian
	# number grouping via fmt_money) and use datatype Data so no other currency symbol
	# is applied on top.
	def card(label, value, indicator):
		return {"label": label, "value": "Rs/- " + fmt_money(flt(value), 2), "datatype": "Data", "indicator": indicator}

	return [
		card(_("Total Actual Payables"), actual, "Red"),
		card(_("Total Received / Unbilled"), unbilled, "Blue"),
		card(_("Total Future Commitments"), future, "Grey"),
		card(_("Total Purchase Exposure"), exposure, "Purple"),
		card(_("Overdue"), overdue, "Red"),
		card(_("Due in 7 Days"), due_within(7), "Orange"),
		card(_("Due in 30 Days"), due_within(30), "Yellow"),
		card(_("Due in 60 Days"), due_within(60), "Green"),
		card(_("Due in 90 Days"), due_within(90), "Green"),
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
