# playground/playground/patches/v0_0/remove_role_permission_matrix_doctypes.py
"""
Retires the short-lived "Role Permission Matrix" feature (added in 0.0.37 and
superseded by the Role Profile permission workbook in 0.0.38).

Deletes the "Role Permission Matrix" and "Role Permission Matrix Child" DocTypes
(and their tables/records) if they exist, so sites that already migrated 0.0.37
don't keep orphaned doctypes once the code is removed. No-op on sites that never
had them. Any Custom DocPerm records the old tool applied are left as-is — they
are ordinary permissions now and unrelated to these doctypes.
"""
import frappe


def execute():
	# Parent before child: the parent references the child as its table option.
	for doctype in ("Role Permission Matrix", "Role Permission Matrix Child"):
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True, ignore_missing=True)
