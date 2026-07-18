# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Invoice override - Price Adjustment Debit Note GRNI reclassification
============================================================================

Handles supplier Debit Notes issued purely for a retrospective price/rate
reduction (no physical return): goods were already received (Purchase Receipt)
and the original invoice booked, so the original PR<->PI pair already cleared
Stock Received But Not Billed (GRNI). ERPNext's standard Debit Note credits GRNI
again, leaving an unwanted residual credit there.

When `custom_is_price_adjustment_debit_note` is ticked on a Debit Note, this
adds two GL rows that move the base purchase value out of GRNI:

    Dr  Stock Received But Not Billed   <GRNI value>
        Cr  Purchase Rate Adjustment    <GRNI value>

so GRNI nets to zero and the price reduction is recognised in the P&L
(Purchase Rate Adjustment). Supplier/Creditors and Input GST keep the standard
Debit Note treatment.

Why this design (vs a separate Journal Entry):
- The rows are appended in get_gl_entries(), so they are part of the Purchase
  Invoice's own GL posting - they survive GL REPOST (repost calls get_gl_entries
  again) and are REVERSED automatically on cancel (ERPNext reverses all GL for
  the voucher). No orphaned entries, no post-hoc GL edits, no Stock Ledger
  Entries.
- Wired via the `override_doctype_class` hook (a supported Frappe extension) -
  no ERPNext core edit, no monkey-patch.
- The reclass amount is DERIVED from the GRNI GL rows the standard posting
  produced, so it excludes GST/TDS/rounding/other-charges automatically, and it
  inherits each GRNI row's accounting dimensions (cost center, project, custom
  dimensions).

VERIFY ON YOUR BENCH (ERPNext v15.115.0 - not inspectable from this repo):
- PurchaseInvoice.get_gl_entries(self, warehouse_account=None) signature.
- Company field `stock_received_but_not_billed` holds the GRNI account.
- If the Frontec app already overrides Purchase Invoice via override_doctype_class,
  merge this logic into that subclass instead - only ONE app may own the class.
"""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.accounts.doctype.purchase_invoice.purchase_invoice import PurchaseInvoice

FLAG_FIELD = "custom_is_price_adjustment_debit_note"
ADJ_ACCOUNT_COMPANY_FIELD = "custom_purchase_rate_adjustment_account"


class CustomPurchaseInvoice(PurchaseInvoice):
	def validate(self):
		super().validate()
		self._validate_price_adjustment_debit_note()

	# --- validation ------------------------------------------------------- #
	def _validate_price_adjustment_debit_note(self):
		if not self.get(FLAG_FIELD):
			return
		if not self.get("is_return"):
			frappe.throw(_("'Is Price Adjustment Debit Note' can only be set on a Debit Note (Is Return)."))
		if not self.get("return_against"):
			frappe.throw(_("A Price Adjustment Debit Note must specify Return Against (the original Purchase Invoice)."))
		if self.get("update_stock"):
			frappe.throw(
				_("Price Adjustment Debit Notes cannot update stock. Use a standard Purchase Return for physical material returns.")
			)
		adj = frappe.get_cached_value("Company", self.company, ADJ_ACCOUNT_COMPANY_FIELD)
		if not adj:
			frappe.throw(
				_("Set a Purchase Rate Adjustment Account on Company {0} before submitting a Price Adjustment Debit Note.").format(self.company)
			)
		if frappe.get_cached_value("Account", adj, "company") != self.company:
			frappe.throw(
				_("The Purchase Rate Adjustment Account {0} does not belong to Company {1}.").format(adj, self.company)
			)

	# --- GL posting ------------------------------------------------------- #
	def get_gl_entries(self, warehouse_account=None):
		gl_entries = super().get_gl_entries(warehouse_account)
		if self.get("is_return") and self.get(FLAG_FIELD):
			gl_entries += self._price_adjustment_reclass_entries(gl_entries)
		return gl_entries

	def _price_adjustment_reclass_entries(self, gl_entries):
		grni = frappe.get_cached_value("Company", self.company, "stock_received_but_not_billed")
		adj = frappe.get_cached_value("Company", self.company, ADJ_ACCOUNT_COMPANY_FIELD)
		if not grni or not adj:
			# Validation already blocks submit without these; guard defensively
			# (e.g. a repost path) rather than posting a half reclassification.
			return []

		dims = _accounting_dimensions()
		extra = []
		for gle in gl_entries:
			if gle.get("account") != grni:
				continue
			# Net movement the standard posting put on GRNI (a Debit Note credits
			# it). Only the base purchase value lands here - GST/TDS/rounding/other
			# charges sit on their own accounts and are untouched.
			net_credit = flt(gle.get("credit")) - flt(gle.get("debit"))
			if abs(net_credit) < 0.005:
				continue

			# Inherit the source GRNI row's dimensions so the reclass carries the
			# same cost center / project / custom accounting dimensions.
			dim_args = {"cost_center": gle.get("cost_center"), "project": gle.get("project")}
			for d in dims:
				if gle.get(d) is not None:
					dim_args[d] = gle.get(d)

			remarks = _("Price adjustment reclassification of GRNI (Debit Note {0}).").format(self.name)

			# Row 1: reverse the GRNI movement (Dr GRNI when it was credited).
			extra.append(self.get_gl_dict({
				"account": grni,
				"debit": net_credit if net_credit > 0 else 0.0,
				"debit_in_account_currency": net_credit if net_credit > 0 else 0.0,
				"credit": -net_credit if net_credit < 0 else 0.0,
				"credit_in_account_currency": -net_credit if net_credit < 0 else 0.0,
				"remarks": remarks,
				**dim_args,
			}, item=None))
			# Row 2: recognise the price reduction in Purchase Rate Adjustment.
			extra.append(self.get_gl_dict({
				"account": adj,
				"credit": net_credit if net_credit > 0 else 0.0,
				"credit_in_account_currency": net_credit if net_credit > 0 else 0.0,
				"debit": -net_credit if net_credit < 0 else 0.0,
				"debit_in_account_currency": -net_credit if net_credit < 0 else 0.0,
				"remarks": remarks,
				**dim_args,
			}, item=None))
		return extra


def _accounting_dimensions():
	"""Custom accounting-dimension fieldnames to carry across, if the module is
	present."""
	try:
		from erpnext.accounts.doctype.accounting_dimension.accounting_dimension import (
			get_accounting_dimensions,
		)

		return get_accounting_dimensions() or []
	except Exception:
		return []
