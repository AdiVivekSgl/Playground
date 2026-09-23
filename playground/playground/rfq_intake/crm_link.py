# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Mirror each intake Opportunity into the Gmail-sidebar CRM layer.

The native Opportunity stays the record of truth (territory, attachments, AI fields);
this keeps a linked CRM Opportunity (via its ``erpnext_opportunity`` link), a CRM Contact
mapping and a CRM Activity entry so the RFQ shows up in the Gmail sidebar too.
"""

import frappe
from frappe.utils import get_datetime

from playground.gmail import _create_crm_contact_mapping

STAGES = ["New", "Contacted", "Requirement", "Quotation", "Negotiation", "PO Expected", "Won", "Lost"]


def _forwarder_user(comm) -> str | None:
	email = (comm.sender or "").strip().lower()
	return email if email and frappe.db.exists("User", {"name": email, "enabled": 1}) else None


def _crm_contact(data: dict, opp, owner: str | None) -> str | None:
	email = data.get("customer_email")
	if not email:
		return None
	customer = opp.party_name if opp.opportunity_from == "Customer" else None
	mapping = _create_crm_contact_mapping(
		email=email, contact_name=data.get("contact_name"), erpnext_customer=customer
	)
	updates = {}
	if opp.opportunity_from == "Lead":
		updates["erpnext_lead"] = opp.party_name
	if owner and frappe.db.get_value("CRM Contact", mapping["name"], "sales_owner") in (None, "", "Administrator"):
		updates["sales_owner"] = owner
	if updates:
		frappe.db.set_value("CRM Contact", mapping["name"], updates)
	return mapping["name"]


def sync(opp, data: dict, comm) -> str:
	"""Create or update the CRM Opportunity linked to ``opp``; log a CRM Activity. Returns its name."""
	owner = _forwarder_user(comm)
	customer = opp.party_name if opp.opportunity_from == "Customer" else None
	is_quote = data["doc_type"] == "our_quotation"

	name = frappe.db.get_value("CRM Opportunity", {"erpnext_opportunity": opp.name}, "name")
	if name:
		crm = frappe.get_doc("CRM Opportunity", name)
		if data.get("overall_value") is not None:
			crm.value = opp.opportunity_amount
		if opp.expected_closing:
			crm.expected_closing = opp.expected_closing
		# Only move forward in the funnel, never back.
		if is_quote and STAGES.index(crm.stage or "New") < STAGES.index("Quotation"):
			crm.stage = "Quotation"
		crm.save(ignore_permissions=True)
	else:
		crm = frappe.new_doc("CRM Opportunity")
		crm.opportunity_name = opp.title
		crm.customer = customer
		crm.crm_contact = _crm_contact(data, opp, owner)
		crm.value = opp.opportunity_amount
		crm.expected_closing = opp.expected_closing
		crm.stage = "Quotation" if is_quote else "New"
		crm.source = "Gmail"
		crm.sales_owner = owner or frappe.session.user
		crm.erpnext_opportunity = opp.name
		crm.insert(ignore_permissions=True)

	act = frappe.new_doc("CRM Activity")
	act.activity_type = "Email"
	act.activity_datetime = get_datetime(comm.communication_date)
	act.description = (
		f"{'Our quotation' if is_quote else 'RFQ'} logged via email intake: {opp.title} "
		f"(Opportunity {opp.name})."
	)
	act.crm_opportunity = crm.name
	act.customer = customer
	act.user = owner or frappe.session.user
	act.reference_doctype = "Communication"
	act.reference_name = comm.name
	act.insert(ignore_permissions=True)
	return crm.name
