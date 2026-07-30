# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Expense Provision
=================

A generic month-end provision (accrual) for a recurring / estimated expense -
electricity, bank interest, freight, audit fees, professional fees, etc.

Lifecycle
---------
  1. Create + submit. On submit the provision Journal Entry is booked:

         Electricity Expense    Dr  1,00,000
             Provision for Electricity  Cr  1,00,000

     and status becomes Open.

  2. When the actual document arrives, the user links this provision from it via
     the "Provision Against" field:
       - Purchase Invoice  -> settlement + GL reclass handled in the PI override
         (playground.playground.overrides.purchase_invoice).
       - Journal Entry      -> settlement tracked from the JE's own posting
         (playground.playground.provision_management).
     Each linked, submitted document appends a row to `settlements`, and
     `utilized_amount` / `outstanding_amount` / `status` recompute here.

  3. Status walks Open -> Partially Settled -> Settled as the outstanding balance
     is consumed. Over-provision (actual > provision) books the excess to the
     expense account on the actual document; under-provision leaves a positive
     outstanding balance you can see in the Outstanding Expense Provisions report.

Reversal mode
-------------
  - "Match and Settle" (default): keep the provision Open until actuals are matched
    - explicit provision-to-invoice matching.
  - "Auto-Reverse": the monthly scheduler reverses any unsettled balance at the
    start of the next month (classic accrual reversal). See
    playground.playground.provision_management.auto_reverse_due_provisions.

VERIFY ON YOUR BENCH (ERPNext v15 - not inspectable from this repo):
  - Journal Entry field names used here: `voucher_type`, `posting_date`,
    `company`, `user_remark`, and account rows `account` / `debit_in_account_currency`
    / `credit_in_account_currency` / `cost_center` / `project` / `party_type` / `party`.
  - That the provision & expense accounts belong to `company` and are not Group
    accounts (validated below, but confirm your CoA setup).
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from playground.playground.provision_management import party_kwargs, recompute_provision


class ExpenseProvision(Document):
	def validate(self):
		self._validate_accounts()
		# In draft, keep the rollups honest for display; the authoritative
		# recompute happens through recompute_provision() as settlements land.
		self.utilized_amount = sum(flt(s.settled_amount) for s in self.settlements)
		self.outstanding_amount = flt(self.provision_amount) - flt(self.utilized_amount)
		if self.docstatus == 0:
			self.status = "Draft"

	def _validate_accounts(self):
		if flt(self.provision_amount) <= 0:
			frappe.throw(_("Provision Amount must be greater than zero."))

		for fieldname, label in (("expense_account", _("Expense Account")), ("provision_account", _("Provision Account"))):
			account = self.get(fieldname)
			company, is_group = frappe.db.get_value("Account", account, ["company", "is_group"]) or (None, None)
			if company != self.company:
				frappe.throw(_("{0} {1} does not belong to Company {2}.").format(label, account, self.company))
			if is_group:
				frappe.throw(_("{0} {1} is a Group account - pick a ledger account.").format(label, account))

		if self.expense_account == self.provision_account:
			frappe.throw(_("Expense Account and Provision Account must be different."))

		if self.party_type and not self.party:
			frappe.throw(_("Select a Party for Party Type {0}, or clear the Party Type.").format(self.party_type))

	def on_submit(self):
		je = self._make_provision_je()
		self.db_set("provision_journal_entry", je)
		self.db_set("status", "Open")

	def on_cancel(self):
		# Don't let a provision be cancelled out from under actuals that were matched
		# against it - unwind those first so the GL stays coherent.
		if self.settlements:
			frappe.throw(
				_("This provision has {0} settlement(s). Cancel the linked actual document(s) first.").format(
					len(self.settlements)
				)
			)
		self._cancel_provision_je()
		self.db_set("status", "Cancelled")

	# ------------------------------------------------------------------ #
	# Provision Journal Entry
	# ------------------------------------------------------------------ #
	def _make_provision_je(self):
		"""Book Expense Dr / Provision Cr for the full provision amount. Returns the
		submitted Journal Entry name."""
		amount = flt(self.provision_amount)
		je = frappe.new_doc("Journal Entry")
		je.voucher_type = "Journal Entry"
		je.company = self.company
		je.posting_date = self.posting_date
		je.user_remark = _("Provision for {0} ({1}).").format(self.expense_account, self.name)

		# Expense leg (Dr) - carries the cost center / project accounting dimensions.
		je.append("accounts", {
			"account": self.expense_account,
			"debit_in_account_currency": amount,
			"cost_center": self.cost_center,
			"project": self.project,
		})
		# Provision / liability leg (Cr) - party only if it's a Payable/Receivable
		# account (party_kwargs enforces ERPNext's rule).
		je.append("accounts", {
			"account": self.provision_account,
			"credit_in_account_currency": amount,
			**party_kwargs(self),
		})

		je.flags.ignore_permissions = True
		je.insert()
		je.submit()
		return je.name

	def _cancel_provision_je(self):
		if not self.provision_journal_entry:
			return
		if not frappe.db.exists("Journal Entry", self.provision_journal_entry):
			return
		je = frappe.get_doc("Journal Entry", self.provision_journal_entry)
		if je.docstatus == 1:
			je.flags.ignore_permissions = True
			je.cancel()


@frappe.whitelist()
def refresh_status(provision_name):
	"""Manual recompute hook (button / console) - re-derives utilized, outstanding
	and status from the current settlement rows."""
	recompute_provision(provision_name)
	return frappe.db.get_value(
		"Expense Provision", provision_name,
		["status", "utilized_amount", "outstanding_amount"], as_dict=True,
	)
