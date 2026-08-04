# playground/playground/patches/v0_0/drop_expense_provision_settlement.py
"""
Retires the "Expense Provision Settlement" child DocType.

The Provision Management feature originally tracked partial settlements against a
provision in a child table. The model changed to full reversal on the first linked
actual document (no partial settlement / carry-forward), so the child table is
gone. This deletes the DocType (and its table) on sites that already migrated an
earlier build where it existed. No-op on sites that never had it.
"""
import frappe


def execute():
	if frappe.db.exists("DocType", "Expense Provision Settlement"):
		frappe.delete_doc("DocType", "Expense Provision Settlement", force=True, ignore_permissions=True, ignore_missing=True)
