# playground/playground/patches/v0_0/remove_custom_sales_order_statuses.py
"""
Reverts the "Ready for Dispatch" / "Inspected" Sales Order status
customization: removes those two options from the Property Setter that
add_custom_sales_order_statuses.py created (that patch module has since been
deleted from this app), leaving any other options already on the site
untouched. Deletes the Property Setter entirely if nothing else is left in
its value after removal, so no leftover artifact remains.

Does NOT change any Sales Order that currently has status = "Ready for
Dispatch" or "Inspected" already saved on it - those documents keep whatever
value is in the DB; only the dropdown's available options are being reverted.
"""
import frappe


def execute():
	doctype = "Sales Order"
	fieldname = "status"
	remove_statuses = {"Ready for Dispatch", "Inspected"}

	ps_name = frappe.db.get_value(
		"Property Setter",
		{"doc_type": doctype, "field_name": fieldname, "property": "options"},
		"name",
	)
	if not ps_name:
		return

	ps = frappe.get_doc("Property Setter", ps_name)
	current_options = [o for o in (ps.value or "").split("\n") if o]
	new_options = [o for o in current_options if o not in remove_statuses]

	if new_options == current_options:
		return

	if new_options:
		ps.value = "\n".join(new_options)
		ps.save()
	else:
		frappe.delete_doc("Property Setter", ps_name, ignore_permissions=True)
