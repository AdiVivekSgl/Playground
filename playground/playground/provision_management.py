# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Provision Management - full-reversal settlement
===============================================

When an actual document (Purchase Invoice or Journal Entry) is linked to an open
Expense Provision via the "Provision Against" field, the provision is reversed IN
FULL on that document's posting date - no consumption, no carry-forward, no
variance. The actual document books its own expense normally; the reversal simply
unwinds the whole accrual.

  Provision (31-Mar):
      Electricity Expense    Dr  1,00,000
          Provision for Electricity  Cr  1,00,000

  Actual invoice (April, PI 1,08,000) - posted normally, untouched:
      Electricity Expense    Dr  1,08,000
          Creditors              Cr  1,08,000

  Full reversal (auto, on the invoice's date):
      Provision for Electricity  Dr  1,00,000
          Electricity Expense        Cr  1,00,000

Net P&L = 1,00,000 (Mar) + 1,08,000 - 1,00,000 (Apr) = 1,08,000 (the actual); the
provision liability nets to zero. Each provision reverses exactly once - the link
field only offers Open provisions, and the server blocks a second link.

Wiring:
  - Purchase Invoice -> CustomPurchaseInvoice.on_submit/on_cancel
    (playground.playground.overrides.purchase_invoice).
  - Journal Entry     -> doc_events in hooks.py.

The reversal is posted as its own Journal Entry (not injected into the triggering
document's GL), so it is explicit, auditable, and cancels cleanly when the
triggering document is cancelled.

Expense Provision references its Journal Entries and the triggering document by
NAME in plain Data fields (provision_journal_entry / reversal_journal_entry /
reversed_against), NOT as Link / Dynamic Link. That is deliberate: a hard Link
would make ERPNext block cancellation of those JEs / documents with an "is linked
with Expense Provision" error (and would even break the auto-undo below, which
cancels the reversal JE). Storing the name as text decouples them, so the JEs and
the triggering document can be cancelled freely; the auto-undo still finds them by
name.

LINK FIELD (`custom_provision_against`) is created on Purchase Invoice and Journal
Entry by create_provision_custom_fields() (after_migrate in hooks.py).
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

LINK_FIELD = "custom_provision_against"


def party_kwargs(provision):
	"""Party fields for the provision (liability) leg - included ONLY when the
	provision account is a Payable/Receivable account. ERPNext rejects a party on
	any other account type, so a plain 'Provision for Expenses' liability carries no
	party even when the provision names one (the party still shows on the document)."""
	if not (provision.party_type and provision.party):
		return {}
	acc_type = frappe.get_cached_value("Account", provision.provision_account, "account_type")
	if acc_type in ("Payable", "Receivable"):
		return {"party_type": provision.party_type, "party": provision.party}
	return {}


def _get_linked_provision(doc):
	name = doc.get(LINK_FIELD)
	if not name:
		return None
	return frappe.get_doc("Expense Provision", name)


# --------------------------------------------------------------------------- #
# Validation (called from PI / JE validate)
# --------------------------------------------------------------------------- #
def validate_provision_link(doc, method=None):
	"""Only an Open provision of the same company may be linked. The link field is
	already filtered to Open provisions in the UI; this is the server-side guard."""
	provision = _get_linked_provision(doc)
	if not provision:
		return
	if provision.company != doc.get("company"):
		frappe.throw(_("Provision {0} belongs to a different company.").format(provision.name))
	# Idempotent: a re-validate of the very voucher that reversed it is fine.
	if provision.status == "Reversed" and provision.reversed_against == doc.name:
		return
	if provision.status != "Open":
		frappe.throw(
			_("Provision {0} is {1}, not Open. Select an open (un-reversed) provision.").format(
				provision.name, provision.status
			)
		)


# --------------------------------------------------------------------------- #
# Reverse / undo (called on submit / cancel of the actual voucher)
# --------------------------------------------------------------------------- #
def reverse_provision_for(doc, voucher_type):
	"""Reverse the linked provision IN FULL on the voucher's posting date. One-time:
	a provision reverses exactly once."""
	provision = _get_linked_provision(doc)
	if not provision:
		return
	if provision.status == "Reversed":
		# Already reversed by this same voucher (idempotent) -> nothing to do.
		if provision.reversed_against == doc.name and provision.reversed_against_type == voucher_type:
			return
		frappe.throw(_("Provision {0} is already reversed.").format(provision.name))
	if provision.status != "Open":
		frappe.throw(_("Provision {0} is not Open; cannot reverse.").format(provision.name))

	posting_date = doc.get("posting_date") or nowdate()
	je = _make_reversal_je(provision, posting_date, voucher_type, doc.name)
	provision.db_set("reversal_journal_entry", je, update_modified=False)
	provision.db_set("reversed_on", posting_date, update_modified=False)
	provision.db_set("reversed_against_type", voucher_type, update_modified=False)
	provision.db_set("reversed_against", doc.name, update_modified=False)
	provision.db_set("status", "Reversed", update_modified=False)


def undo_reverse_for(doc, voucher_type):
	"""On cancel of the triggering voucher: cancel the reversal JE and reopen the
	provision. No-op if this voucher isn't the one that reversed it."""
	provision = _get_linked_provision(doc)
	if not provision:
		return
	if provision.reversed_against != doc.name or provision.reversed_against_type != voucher_type:
		return
	_cancel_je(provision.reversal_journal_entry)
	provision.db_set("reversal_journal_entry", None, update_modified=False)
	provision.db_set("reversed_on", None, update_modified=False)
	provision.db_set("reversed_against_type", None, update_modified=False)
	provision.db_set("reversed_against", None, update_modified=False)
	provision.db_set("status", "Open", update_modified=False)


def _make_reversal_je(provision, posting_date, voucher_type, voucher_no):
	"""Book Provision Dr / Expense Cr for the FULL provision amount. Returns the
	submitted Journal Entry name."""
	amount = flt(provision.provision_amount)
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = provision.company
	je.posting_date = posting_date
	je.user_remark = _("Full reversal of provision {0} against {1} {2}.").format(
		provision.name, voucher_type, voucher_no
	)
	# Provision / liability leg (Dr) - unwind the accrual.
	je.append("accounts", {
		"account": provision.provision_account,
		"debit_in_account_currency": amount,
		**party_kwargs(provision),
	})
	# Expense leg (Cr) - remove the estimate (the actual sits on the triggering doc).
	je.append("accounts", {
		"account": provision.expense_account,
		"credit_in_account_currency": amount,
		"cost_center": provision.cost_center,
		"project": provision.project,
	})
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je.name


def _cancel_je(je_name):
	if not je_name or not frappe.db.exists("Journal Entry", je_name):
		return
	je = frappe.get_doc("Journal Entry", je_name)
	if je.docstatus == 1:
		je.flags.ignore_permissions = True
		je.cancel()


# --------------------------------------------------------------------------- #
# Journal Entry doc_events (wired in hooks.py)
# --------------------------------------------------------------------------- #
def on_journal_entry_validate(doc, method=None):
	validate_provision_link(doc)


def on_journal_entry_submit(doc, method=None):
	reverse_provision_for(doc, "Journal Entry")


def on_journal_entry_cancel(doc, method=None):
	undo_reverse_for(doc, "Journal Entry")


# --------------------------------------------------------------------------- #
# Custom field installer (after_migrate in hooks.py)
# --------------------------------------------------------------------------- #
def create_provision_custom_fields():
	"""Create the 'Provision Against' link on Purchase Invoice and Journal Entry.
	Idempotent - safe to run on every migrate.

	Runs as an after_migrate hook, so it is wrapped defensively: a failure here must
	never abort migrate (on Frappe Cloud a failed migrate rolls the whole DB back).
	If field creation fails it is logged and the migrate still completes; the field
	can then be created on a later migrate or manually."""
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	field = {
		"fieldname": LINK_FIELD,
		"label": "Provision Against",
		"fieldtype": "Link",
		"options": "Expense Provision",
		"insert_after": "company",
		"description": (
			"Link an open Expense Provision to reverse it in full against this "
			"document (on submit, dated this document's posting date)."
		),
	}
	try:
		create_custom_fields({"Purchase Invoice": [field], "Journal Entry": [dict(field)]}, ignore_validate=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "create_provision_custom_fields failed")
