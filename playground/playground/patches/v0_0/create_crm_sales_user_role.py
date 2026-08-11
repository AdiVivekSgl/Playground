# playground/playground/patches/v0_0/create_crm_sales_user_role.py
import frappe


def execute():
	# The CRM DocTypes grant permissions to a "Sales User" role, which Frappe
	# will not auto-create from a permission row. Ensure it exists before those
	# DocTypes are (re)synced on migrate.
	if not frappe.db.exists("Role", "Sales User"):
		frappe.get_doc(
			{
				"doctype": "Role",
				"role_name": "Sales User",
				"desk_access": 1,
			}
		).insert(ignore_permissions=True)
