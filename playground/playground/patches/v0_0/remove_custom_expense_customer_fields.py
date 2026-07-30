# playground/playground/patches/v0_0/remove_custom_expense_customer_fields.py
"""
Retires the `custom_expense_customer` grouping field from Journal Entry and
Purchase Invoice. It was a manual Link -> Customer used to group non-COGS
expenses, but it is redundant now that expenses are tagged to specific
Sales/Purchase Invoices via the Linked Sales/Purchase Invoice Table
MultiSelect fields - the customer (and supplier) can be derived from the
linked invoices at report time instead of being stored on the expense doc.

Deletes the two Custom Field definitions so the fields disappear from the
forms. Does NOT drop the physical `custom_expense_customer` columns on
`tabJournal Entry` / `tabPurchase Invoice`: Frappe leaves them as orphan
columns, so any values already entered survive there and remain recoverable
until the columns are dropped manually. No document data is rewritten.
"""
import frappe

CUSTOM_FIELDS = (
	"Journal Entry-custom_expense_customer",
	"Purchase Invoice-custom_expense_customer",
)


def execute():
	for name in CUSTOM_FIELDS:
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True)
