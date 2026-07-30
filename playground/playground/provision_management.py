# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Provision Management - settlement engine
========================================

Shared logic that matches *actual* documents against an open Expense Provision.
Two entry points, wired from the actual documents (not from Expense Provision):

  - Purchase Invoice  -> apply_pi_settlement / remove_pi_settlement, called from
    CustomPurchaseInvoice (playground.playground.overrides.purchase_invoice). The
    PI override also appends the GL reclass rows - see provision_reclass_gl_entries.

  - Journal Entry      -> apply_je_settlement / remove_je_settlement, wired via
    doc_events in hooks.py.

Settlement model
----------------
The user links a provision from the actual document's "Provision Against"
(`custom_provision_against`) field. On submit we append one Expense Provision
Settlement row recording how much of the provision that document consumed, then
recompute the provision's utilized / outstanding / status.

Consumption is capped at the provision's remaining balance and is computed
*excluding this voucher's own settlement row*, so it is stable across GL repost
and idempotent re-runs:

    available = provision_amount - utilized_by_other_vouchers
    consumed  = clamp(actual_expense_on_this_voucher, 0, available)

Worked example (partial settlement): provision 1,00,000; invoice A of 60,000
consumes 60,000 (outstanding 40,000 -> Partially Settled); invoice B of 48,000
consumes the remaining 40,000 and books 8,000 as extra expense (variance 8,000 ->
Settled).

Purchase Invoice - accounting effect
------------------------------------
The PI posts normally (Expense Dr <total> / Creditor Cr <total>); the override
then appends a reclass that moves the *consumed* portion off the expense account
onto the provision (liability) account:

    Provision for Electricity  Dr  <consumed>
        Electricity Expense        Cr  <consumed>

Net GL for a 1,08,000 invoice against a 1,00,000 provision:
    Electricity Expense    Dr    8,000   (only the over-provision hits P&L)
    Provision for Elec.    Dr  1,00,000  (liability cleared)
        Creditor               Cr  1,08,000

Doing it as appended rows inside the PI's own get_gl_entries (rather than a
separate JE) means it survives GL repost and is reversed automatically on cancel
- the same pattern this app already uses for the Price Adjustment Debit Note.

Journal Entry - accounting effect
---------------------------------
For a JE (e.g. bank interest, no Purchase Invoice) the user books the GL directly,
debiting the provision account themselves. We do NOT rewrite the JE; we read how
much it debited to the provision account and record that as the settled amount, so
tracking always reflects the real posting.

LINK FIELD (`custom_provision_against`) is created on Purchase Invoice and Journal
Entry by create_provision_custom_fields(), wired to after_migrate in hooks.py.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

LINK_FIELD = "custom_provision_against"
_SETTLE_EPS = 0.005


# --------------------------------------------------------------------------- #
# Consumption maths
# --------------------------------------------------------------------------- #
def _available_excluding(provision, voucher_type, voucher_no):
	"""Provision balance available to a voucher = provision amount less everything
	consumed by *other* vouchers. Excluding this voucher's own row makes the value
	stable across repost / re-submit."""
	used_by_others = sum(
		flt(s.settled_amount)
		for s in provision.settlements
		if not (s.voucher_type == voucher_type and s.voucher_no == voucher_no)
	)
	return flt(provision.provision_amount) - used_by_others


def pi_expense_on_account(pi, account):
	"""Base-currency expense this Purchase Invoice books to `account` (its item
	lines' expense account). This is the actual we match against the provision."""
	return sum(flt(d.base_net_amount) for d in pi.get("items", []) if d.get("expense_account") == account)


def pi_consumed_amount(pi, provision):
	"""How much of `provision` this Purchase Invoice consumes: the invoice's expense
	on the provision's expense account, capped at the provision's remaining balance.
	Deterministic and repost-safe (see module docstring)."""
	actual = pi_expense_on_account(pi, provision.expense_account)
	available = _available_excluding(provision, "Purchase Invoice", pi.name)
	return max(0.0, min(actual, max(0.0, available)))


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


def je_debit_to_account(je, account):
	"""Net base-currency debit a Journal Entry posts to `account`."""
	total = 0.0
	for row in je.get("accounts", []):
		if row.get("account") == account:
			total += flt(row.get("debit_in_account_currency")) - flt(row.get("credit_in_account_currency"))
	return total


# --------------------------------------------------------------------------- #
# Settlement row lifecycle
# --------------------------------------------------------------------------- #
def _get_linked_provision(doc):
	name = doc.get(LINK_FIELD)
	if not name:
		return None
	return frappe.get_doc("Expense Provision", name)


def _upsert_settlement(provision, voucher_type, voucher_no, settled, variance, remarks, settlement_date):
	"""Append (or replace) the settlement row for this voucher, then recompute."""
	provision.set(
		"settlements",
		[s for s in provision.settlements if not (s.voucher_type == voucher_type and s.voucher_no == voucher_no)],
	)
	if flt(settled) > _SETTLE_EPS or flt(variance) > _SETTLE_EPS:
		provision.append("settlements", {
			"settlement_date": settlement_date or nowdate(),
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"settled_amount": flt(settled),
			"variance": flt(variance),
			"remarks": remarks,
		})
	_persist_and_recompute(provision)


def _remove_settlement(provision, voucher_type, voucher_no):
	before = len(provision.settlements)
	provision.set(
		"settlements",
		[s for s in provision.settlements if not (s.voucher_type == voucher_type and s.voucher_no == voucher_no)],
	)
	if len(provision.settlements) != before:
		_persist_and_recompute(provision)


def _persist_and_recompute(provision):
	"""Persist the settlement child table on a submitted provision and refresh the
	rollups + status. `settlements` is allow_on_submit so this is legal post-submit."""
	provision.flags.ignore_validate_update_after_submit = True
	provision.flags.ignore_permissions = True
	# Rewrite the child table rows against the stored parent.
	provision.save()
	recompute_provision(provision.name)


def recompute_provision(provision_name):
	"""Re-derive utilized / outstanding / status from the current settlement rows."""
	provision = frappe.get_doc("Expense Provision", provision_name)
	if provision.docstatus == 2:
		return
	utilized = sum(flt(s.settled_amount) for s in provision.settlements)
	outstanding = flt(provision.provision_amount) - utilized
	status = _status_for(provision, utilized, outstanding)
	provision.db_set("utilized_amount", utilized, update_modified=False)
	provision.db_set("outstanding_amount", outstanding, update_modified=False)
	provision.db_set("status", status, update_modified=False)


def _status_for(provision, utilized, outstanding):
	if provision.status == "Reversed":
		return "Reversed"
	if utilized <= _SETTLE_EPS:
		return "Open"
	if outstanding > _SETTLE_EPS:
		return "Partially Settled"
	return "Settled"


# --------------------------------------------------------------------------- #
# Purchase Invoice entry points (called from CustomPurchaseInvoice)
# --------------------------------------------------------------------------- #
def apply_pi_settlement(pi):
	provision = _get_linked_provision(pi)
	if not provision:
		return
	_guard_actual(pi, provision)
	consumed = pi_consumed_amount(pi, provision)
	actual = pi_expense_on_account(pi, provision.expense_account)
	_upsert_settlement(
		provision, "Purchase Invoice", pi.name,
		settled=consumed, variance=actual - consumed,
		remarks=_("Auto-settled from Purchase Invoice."),
		settlement_date=pi.get("posting_date"),
	)


def remove_pi_settlement(pi):
	provision = _get_linked_provision(pi)
	if not provision:
		return
	_remove_settlement(provision, "Purchase Invoice", pi.name)


def provision_reclass_gl_entries(pi, get_gl_dict):
	"""GL rows that move the consumed portion from the expense account to the
	provision account. Called from CustomPurchaseInvoice.get_gl_entries. `get_gl_dict`
	is the PI's bound get_gl_dict method. Returns a list of GL dicts (possibly empty)."""
	provision = _get_linked_provision(pi)
	if not provision:
		return []
	consumed = pi_consumed_amount(pi, provision)
	if consumed <= _SETTLE_EPS:
		return []

	remarks = _("Provision settlement against {0}.").format(provision.name)
	dims = {"cost_center": provision.cost_center, "project": provision.project}
	return [
		get_gl_dict({
			"account": provision.provision_account,
			"debit": consumed,
			"debit_in_account_currency": consumed,
			"remarks": remarks,
			**party_kwargs(provision),
		}, item=None),
		get_gl_dict({
			"account": provision.expense_account,
			"credit": consumed,
			"credit_in_account_currency": consumed,
			"remarks": remarks,
			**dims,
		}, item=None),
	]


def _guard_actual(pi, provision):
	if provision.docstatus != 1:
		frappe.throw(_("Provision {0} is not submitted.").format(provision.name))
	if provision.status in ("Settled", "Reversed", "Cancelled"):
		frappe.throw(_("Provision {0} is {1} and cannot be settled further.").format(provision.name, provision.status))
	if provision.company != pi.company:
		frappe.throw(_("Provision {0} belongs to a different company.").format(provision.name))


# --------------------------------------------------------------------------- #
# Journal Entry entry points (wired via doc_events in hooks.py)
# --------------------------------------------------------------------------- #
def on_journal_entry_validate(doc, method=None):
	provision = _get_linked_provision(doc)
	if not provision:
		return
	if provision.docstatus != 1 or provision.status in ("Settled", "Reversed", "Cancelled"):
		frappe.throw(_("Provision {0} is not open for settlement.").format(provision.name))
	if je_debit_to_account(doc, provision.provision_account) <= _SETTLE_EPS:
		frappe.throw(
			_("A Journal Entry linked to provision {0} must debit its provision account {1}.").format(
				provision.name, provision.provision_account
			)
		)


def on_journal_entry_submit(doc, method=None):
	provision = _get_linked_provision(doc)
	if not provision:
		return
	debit = je_debit_to_account(doc, provision.provision_account)
	available = _available_excluding(provision, "Journal Entry", doc.name)
	consumed = max(0.0, min(debit, max(0.0, available)))
	_upsert_settlement(
		provision, "Journal Entry", doc.name,
		settled=consumed, variance=debit - consumed,
		remarks=_("Settled from Journal Entry."),
		settlement_date=doc.get("posting_date"),
	)


def on_journal_entry_cancel(doc, method=None):
	provision = _get_linked_provision(doc)
	if not provision:
		return
	_remove_settlement(provision, "Journal Entry", doc.name)


# --------------------------------------------------------------------------- #
# Auto-Reverse mode (monthly scheduler)
# --------------------------------------------------------------------------- #
def auto_reverse_due_provisions():
	"""Reverse the unsettled balance of every Auto-Reverse provision whose posting
	month has closed. Books Provision Dr / Expense Cr for the outstanding amount and
	marks the provision Reversed. Idempotent - skips anything already Reversed/Settled.

	Wired to the `monthly` scheduler (runs at the start of each month) in hooks.py."""
	from frappe.utils import get_first_day, getdate

	this_month_start = get_first_day(getdate(nowdate()))
	candidates = frappe.get_all(
		"Expense Provision",
		filters={"docstatus": 1, "reversal_mode": "Auto-Reverse", "status": ["in", ["Open", "Partially Settled"]]},
		fields=["name", "posting_date", "outstanding_amount"],
	)
	for c in candidates:
		# Only reverse once the provision's month is over.
		if getdate(c.posting_date) >= this_month_start:
			continue
		if flt(c.outstanding_amount) <= _SETTLE_EPS:
			continue
		try:
			_reverse_provision(c.name, flt(c.outstanding_amount), this_month_start)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"auto_reverse_due_provisions: {c.name}")


def _reverse_provision(provision_name, amount, posting_date):
	provision = frappe.get_doc("Expense Provision", provision_name)
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = provision.company
	je.posting_date = posting_date
	je.user_remark = _("Auto-reversal of provision {0}.").format(provision.name)
	je.append("accounts", {
		"account": provision.provision_account,
		"debit_in_account_currency": amount,
		**party_kwargs(provision),
	})
	je.append("accounts", {
		"account": provision.expense_account,
		"credit_in_account_currency": amount,
		"cost_center": provision.cost_center,
		"project": provision.project,
	})
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	provision.db_set("status", "Reversed", update_modified=False)


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
			"Link an open Expense Provision to settle it against this document. On "
			"submit the consumed amount is matched off the provision."
		),
	}
	try:
		create_custom_fields({"Purchase Invoice": [field], "Journal Entry": [dict(field)]}, ignore_validate=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "create_provision_custom_fields failed")
