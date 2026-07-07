# playground/playground/patches/v0_0/add_custom_sales_order_statuses.py
"""
Adds two custom Select options to Sales Order.status via a Property Setter:
"Ready for Dispatch" and "Inspected" (see playground.playground.sales_order_status
for the doc_events hook that actually sets these).

Reads the CURRENT options from the doctype meta and appends ours rather than
hardcoding the full list - Sales Order's status options vary by ERPNext
version and by other installed apps' own customizations, so hardcoding risks
clobbering options this site already has.
"""
import frappe


def execute():
	doctype = "Sales Order"
	fieldname = "status"
	new_statuses = ["Ready for Dispatch", "Inspected"]

	meta = frappe.get_meta(doctype)
	field = meta.get_field(fieldname)
	current_options = [o for o in (field.options or "").split("\n") if o] if field else []

	for status in new_statuses:
		if status not in current_options:
			current_options.append(status)

	frappe.make_property_setter(
		{
			"doctype": doctype,
			"fieldname": fieldname,
			"property": "options",
			"value": "\n".join(current_options),
			"property_type": "Text",
		},
		validate_fields_for_doctype=False,
	)
