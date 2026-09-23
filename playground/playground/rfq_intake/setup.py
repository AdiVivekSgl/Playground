# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Idempotent provisioning for RFQ Intake, wired into ``after_migrate``.

Creates the "Email Intake" section on Opportunity (create_custom_fields upserts).
Wrapped defensively like the app's other setup hooks: a failure here must never
abort migrate (on Frappe Cloud a failed migrate rolls the whole DB back); it re-runs
on the next migrate.
"""

import frappe

OPPORTUNITY_FIELDS = [
	{
		"fieldname": "custom_rfq_intake_section",
		"fieldtype": "Section Break",
		"label": "Email Intake",
		"collapsible": 1,
		"insert_after": "opportunity_amount",
	},
	{
		"fieldname": "custom_rfq_type",
		"fieldtype": "Select",
		"label": "Intake Type",
		"options": "\nCustomer RFQ\nOur Quote",
		"in_standard_filter": 1,
		"insert_after": "custom_rfq_intake_section",
	},
	{
		"fieldname": "custom_needs_review",
		"fieldtype": "Check",
		"label": "Needs Review",
		"in_list_view": 1,
		"in_standard_filter": 1,
		"description": "Set by email intake when the AI was unsure, the customer is new, or no value was found.",
		"insert_after": "custom_rfq_type",
	},
	{
		"fieldname": "custom_ai_confidence",
		"fieldtype": "Percent",
		"label": "AI Confidence",
		"read_only": 1,
		"insert_after": "custom_needs_review",
	},
	{
		"fieldname": "custom_rfq_reference",
		"fieldtype": "Data",
		"label": "RFQ / Quote Reference",
		"in_standard_filter": 1,
		"insert_after": "custom_ai_confidence",
	},
	{
		"fieldname": "custom_rfq_intake_col",
		"fieldtype": "Column Break",
		"insert_after": "custom_rfq_reference",
	},
	{
		"fieldname": "custom_ai_summary",
		"fieldtype": "Small Text",
		"label": "AI Summary",
		"insert_after": "custom_rfq_intake_col",
	},
	{
		"fieldname": "custom_source_communication",
		"fieldtype": "Link",
		"options": "Communication",
		"label": "Source Email",
		"read_only": 1,
		"insert_after": "custom_ai_summary",
	},
	{
		"fieldname": "custom_source_fingerprint",
		"fieldtype": "Data",
		"label": "Source Fingerprint",
		"hidden": 1,
		"search_index": 1,
		"insert_after": "custom_source_communication",
	},
]


def setup_rfq_intake():
	"""after_migrate hook — create/refresh the Opportunity intake fields."""
	try:
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

		create_custom_fields({"Opportunity": OPPORTUNITY_FIELDS}, ignore_validate=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "setup_rfq_intake failed")
