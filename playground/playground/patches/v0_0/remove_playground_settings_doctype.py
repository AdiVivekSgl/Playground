# playground/playground/patches/v0_0/remove_playground_settings_doctype.py
"""
Retires the short-lived "Open Order View" feature (added in 0.0.45 and removed in
0.0.46).

Deletes the "Playground Settings" single DocType (and its tabSingles rows) if it
exists, so sites that already migrated 0.0.45 don't keep an orphaned doctype once
the code is removed. No-op on sites that never had it.
"""
import frappe


def execute():
	if frappe.db.exists("DocType", "Playground Settings"):
		frappe.delete_doc(
			"DocType",
			"Playground Settings",
			force=True,
			ignore_permissions=True,
			ignore_missing=True,
		)
