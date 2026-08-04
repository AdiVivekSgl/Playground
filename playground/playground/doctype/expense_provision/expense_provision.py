# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Expense Provision
=================

A month-end provision (accrual) for a recurring / estimated expense - electricity,
bank interest, freight, audit fees, professional fees, etc.

Lifecycle
---------
  1. Create + submit. On submit the provision Journal Entry is booked:

         Electricity Expense    Dr  1,00,000
             Provision for Electricity  Cr  1,00,000

     and status becomes Open.

  2. When the actual document arrives, the user links this provision from it via
     the "Provision Against" field (Purchase Invoice or Journal Entry). On submit
     of that document the provision is reversed IN FULL on its posting date:

         Provision for Electricity  Dr  1,00,000
             Electricity Expense        Cr  1,00,000

     status becomes Reversed. The actual document books its own expense normally -
     no partial settlement, no variance. See
     playground.playground.provision_management.

     The link field only offers Open provisions and each provision reverses
     exactly once; cancelling the triggering document reopens the provision.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from playground.playground.provision_management import party_kwargs


class ExpenseProvision(Document):
	def validate(self):
		self._validate_accounts()
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
		# A reversed provision is tied to a submitted actual document - unwind that
		# first (cancelling it reopens the provision), then cancel the provision.
		if self.status == "Reversed":
			frappe.throw(
				_("This provision is already reversed against {0}. Cancel that document first.").format(
					self.reversed_against
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
