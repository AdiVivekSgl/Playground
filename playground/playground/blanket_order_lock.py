# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Sales Order — Blanket Order lock
================================

Business rule: a Sales Order that ORIGINATES FROM A BLANKET ORDER exists only to
draw down against an already-negotiated commitment. The customer's rate and the
commercial terms were settled on the Blanket Order; the SO may vary QUANTITY, and
nothing else. So on such an SO we lock the price and the terms and let qty flow.

An SO "originates from a Blanket Order" when any Sales Order Item line carries a
`blanket_order` link — set automatically when the SO is raised via
*Create -> Sales Order* from a Blanket Order (ERPNext maps Blanket Order Item.rate
onto Sales Order Item.blanket_order_rate and pins the line rate to it).

Two layers, both enforced in `validate` (the authoritative guard — the client-side
grey-out in public/js/sales_order.js is UX only and can be bypassed):

  1. Rate integrity (ABSOLUTE, per blanket line)
     -------------------------------------------
     Every blanket line's `rate` must equal its `blanket_order_rate`. This one
     invariant is the whole price lock: the effective price a customer is charged
     is `rate`, so pinning it to the negotiated blanket rate makes discounting
     powerless — any discount that actually moved the price would move `rate` off
     `blanket_order_rate` and be rejected here, while a cosmetic discount that nets
     back to `blanket_order_rate` charges the agreed price anyway. Because it is an
     absolute check (not a diff against a prior version) it holds on the FIRST save,
     on re-saves, and on amendments alike.

  2. Terms freeze (per header, DIFF against the baseline)
     ----------------------------------------------------
     Currency / Exchange Rate / Price List / Payment Terms Template and the Payment
     Schedule STRUCTURE (term, portion, due date — not the amounts, which legitimately
     rescale when qty changes) may not change once the SO exists. These have no
     blanket-derived "correct" value, so they are frozen relative to a baseline: the
     pre-save version of the doc, or — when the doc is a fresh amendment whose first
     save has no pre-save row yet — the `amended_from` original. A brand-new original
     SO has no baseline, so its terms are set once at creation and frozen thereafter.

Non-blanket Sales Orders are untouched: the whole check returns early when no line
carries a `blanket_order`.
"""

import frappe
from frappe import _
from frappe.utils import flt


# Header fields frozen once the SO exists (label used in the error message).
FROZEN_HEADER_FIELDS = {
	"currency": "Currency",
	"conversion_rate": "Exchange Rate",
	"selling_price_list": "Price List",
	"payment_terms_template": "Payment Terms Template",
}

# Payment Schedule columns that define the TERMS (frozen). Amount columns
# (payment_amount / base_payment_amount) are deliberately excluded — they rescale
# with the grand total when qty changes, which is allowed.
PAYMENT_SCHEDULE_TERM_FIELDS = (
	"payment_term",
	"due_date",
	"invoice_portion",
	"credit_days",
	"mode_of_payment",
)


def enforce_blanket_order_lock(doc, method=None):
	"""Sales Order `validate` hook. No-op unless the SO originates from a Blanket
	Order; otherwise pins every blanket line's rate to its blanket_order_rate and
	freezes the commercial terms against the baseline."""
	if not _has_blanket_line(doc):
		return

	_enforce_line_rates(doc)
	_enforce_frozen_terms(doc)


# --------------------------------------------------------------------------- #
# Origin test
# --------------------------------------------------------------------------- #

def _has_blanket_line(doc):
	"""True when any line links a Blanket Order — the definition of 'originates
	from a Blanket Order'."""
	return any(d.get("blanket_order") for d in doc.get("items", []))


# --------------------------------------------------------------------------- #
# Layer 1 — rate integrity (absolute)
# --------------------------------------------------------------------------- #

def _enforce_line_rates(doc):
	"""Each blanket line's rate must equal its blanket_order_rate. Compared at the
	line's own `rate` precision so ordinary rounding never trips the check. Lines
	without a stored blanket_order_rate have nothing to pin against and are skipped."""
	for d in doc.get("items", []):
		if not d.get("blanket_order"):
			continue
		bo_rate = flt(d.get("blanket_order_rate"))
		if not bo_rate:
			continue
		prec = d.precision("rate") or 2
		if flt(d.get("rate"), prec) != flt(bo_rate, prec):
			frappe.throw(
				_(
					"Row #{0} ({1}): this Sales Order draws down Blanket Order {2}, "
					"so the rate is fixed at the negotiated {3} and cannot be changed "
					"(found {4}). Only the quantity may be edited."
				).format(
					d.idx,
					d.get("item_code"),
					d.get("blanket_order"),
					frappe.format_value(bo_rate, {"fieldtype": "Currency"}),
					frappe.format_value(flt(d.get("rate")), {"fieldtype": "Currency"}),
				),
				title=_("Blanket Order rate is locked"),
			)


# --------------------------------------------------------------------------- #
# Layer 2 — terms freeze (diff against baseline)
# --------------------------------------------------------------------------- #

def _baseline(doc):
	"""The reference version the terms are frozen against: the pre-save doc for an
	ordinary edit, else the `amended_from` original for an amendment's first save,
	else None for a brand-new original (nothing to freeze against yet)."""
	before = doc.get_doc_before_save()
	if before is not None:
		return before
	if doc.get("amended_from"):
		return frappe.get_doc(doc.doctype, doc.amended_from)
	return None


def _enforce_frozen_terms(doc):
	"""Reject any change to the frozen header fields or the Payment Schedule
	structure, relative to the baseline. No baseline (new original) => nothing to
	freeze against."""
	baseline = _baseline(doc)
	if baseline is None:
		return

	for field, label in FROZEN_HEADER_FIELDS.items():
		if not doc.meta.has_field(field):
			continue
		if _field_changed(field, doc.get(field), baseline.get(field)):
			frappe.throw(
				_(
					"{0} cannot be changed on a Sales Order raised from a Blanket "
					"Order — the commercial terms are fixed by the Blanket Order. "
					"Only quantities may be edited."
				).format(_(label)),
				title=_("Blanket Order terms are locked"),
			)

	if _schedule_signature(doc) != _schedule_signature(baseline):
		frappe.throw(
			_(
				"The Payment Schedule cannot be changed on a Sales Order raised from "
				"a Blanket Order — the payment terms are fixed. Only quantities may be "
				"edited (payment amounts rescale with quantity automatically)."
			),
			title=_("Blanket Order terms are locked"),
		)


def _field_changed(field, new_value, old_value):
	"""Change test tolerant of numeric noise: `conversion_rate` compares as a float,
	everything else as a normalized value."""
	if field == "conversion_rate":
		return flt(new_value) != flt(old_value)
	return (new_value or None) != (old_value or None)


def _schedule_signature(doc):
	"""Order-preserving tuple of the Payment Schedule's TERM columns (amounts
	excluded — they rescale with qty). Two docs with the same signature have the
	same payment terms."""
	return [
		tuple(
			flt(row.get(f)) if f in ("invoice_portion", "credit_days")
			else (str(row.get(f)) if row.get(f) else None)
			for f in PAYMENT_SCHEDULE_TERM_FIELDS
		)
		for row in doc.get("payment_schedule", [])
	]
