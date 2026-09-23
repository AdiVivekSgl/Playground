# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Map extracted RFQ data onto native ERPNext: Customer/Lead, Territory, Opportunity.

Customer resolution reuses the Gmail sidebar's lookups (playground.gmail) so both
entry points agree on who a sender is.
"""

import frappe
from frappe.utils import add_days, getdate, nowdate

from playground.gmail import _domain_customers, _find_native_customer_by_email
from playground.playground.rfq_intake.utils import (
	best_match,
	email_domain,
	is_business_domain,
	needs_review,
	similarity,
	summary_text,
)

CUSTOMER_MATCH_THRESHOLD = 0.85
TITLE_MATCH_THRESHOLD = 0.6


# --- party -------------------------------------------------------------------------------------

def _customer_by_name(name: str | None) -> str | None:
	if not name:
		return None
	customers = frappe.get_all("Customer", filters={"disabled": 0}, fields=["name", "customer_name"])
	by_display = {c.customer_name: c.name for c in customers}
	hit = best_match(name, list(by_display), CUSTOMER_MATCH_THRESHOLD)
	return by_display.get(hit) if hit else None


def _find_or_create_lead(data: dict, territory: str) -> tuple[str, bool]:
	email = data.get("customer_email")
	company_name = data.get("customer_name")
	existing = None
	if email:
		existing = frappe.db.get_value("Lead", {"email_id": email}, "name")
	if not existing and company_name:
		existing = frappe.db.get_value("Lead", {"company_name": company_name}, "name")
	if existing:
		return existing, False

	lead = frappe.get_doc({
		"doctype": "Lead",
		"first_name": data.get("contact_name") or company_name or "Unknown",
		"company_name": company_name,
		"email_id": email,
		"territory": territory,
		"source": frappe.db.exists("Lead Source", "Email") and "Email" or None,
	})
	try:
		lead.insert(ignore_permissions=True)
	except frappe.InvalidEmailAddressError:
		lead.email_id = None
		lead.insert(ignore_permissions=True)
	return lead.name, True


def resolve_party(data: dict, own_domains: set[str], territory_fallback: str) -> tuple[str, str, bool]:
	"""Return (party_type, party_name, is_new).

	Order: exact contact email -> the single customer behind a business domain ->
	fuzzy customer name -> existing/new Lead.
	"""
	email = (data.get("customer_email") or "").strip().lower()
	domain = email_domain(email)
	if email and is_business_domain(domain, own_domains):
		_contact, customer = _find_native_customer_by_email(email)
		if customer:
			return "Customer", customer, False
		by_domain = _domain_customers(domain)
		if len(by_domain) == 1:
			return "Customer", by_domain[0]["name"], False
	customer = _customer_by_name(data.get("customer_name"))
	if customer:
		return "Customer", customer, False
	lead, is_new = _find_or_create_lead(data, territory_fallback)
	return "Lead", lead, is_new


# --- territory / currency ----------------------------------------------------------------------

def resolve_territory(data: dict, party_type: str | None, party: str | None, default: str) -> str:
	if party_type == "Customer" and party:
		t = frappe.db.get_value("Customer", party, "territory")
		if t and t != "All Territories":
			return t
	return data.get("territory") or default


def resolve_currency(data: dict, default: str) -> str:
	cur = data.get("currency")
	if cur and frappe.db.exists("Currency", {"name": cur, "enabled": 1}):
		return cur
	return default


# --- opportunity -------------------------------------------------------------------------------

def _find_open_opportunity(party_type: str, party: str, data: dict, window_days: int) -> str | None:
	ref = data.get("our_reference")
	if ref:
		hit = frappe.db.get_value("Opportunity", {"custom_rfq_reference": ref, "docstatus": ["<", 2]}, "name")
		if hit:
			return hit
	candidates = frappe.get_all(
		"Opportunity",
		filters={
			"opportunity_from": party_type,
			"party_name": party,
			"status": ["in", ["Open", "Replied", "Quotation"]],
			"creation": [">=", add_days(nowdate(), -(window_days or 90))],
		},
		fields=["name", "title"],
		order_by="creation desc",
	)
	scored = [(similarity(c.title, data.get("title")), c.name) for c in candidates]
	scored = [s for s in scored if s[0] >= TITLE_MATCH_THRESHOLD]
	if scored:
		return max(scored)[1]
	# Only one open opportunity for this customer: assume that's the one being quoted.
	return candidates[0].name if len(candidates) == 1 else None


def _items(data: dict) -> list[dict]:
	rows = []
	for li in data.get("line_items") or []:
		code = li.get("item_code")
		if not code or not frappe.db.exists("Item", code):
			continue
		rows.append({
			"item_code": code,
			"qty": li.get("qty") or 1,
			"rate": li.get("rate") or 0,
			"uom": frappe.db.get_value("Item", code, "stock_uom"),
		})
	return rows


def upsert_opportunity(data: dict, comm, settings, own_domains: set[str], fingerprint: str) -> tuple[object, bool, bool]:
	"""Create or update the native Opportunity. Returns (doc, created, is_new_party)."""
	party_type, party, is_new_party = resolve_party(data, own_domains, data.get("territory") or settings.default_territory)
	territory = resolve_territory(data, party_type, party, settings.default_territory)
	currency = resolve_currency(data, settings.default_currency)
	review = needs_review(data, is_new_party, settings.review_confidence_threshold or 0.7)
	summary = summary_text(data)

	existing = None
	if data["doc_type"] == "our_quotation":
		existing = _find_open_opportunity(party_type, party, data, settings.match_window_days)

	if existing:
		opp = frappe.get_doc("Opportunity", existing)
		if data.get("overall_value") is not None:
			opp.opportunity_amount = data["overall_value"]
			opp.currency = currency
		if data.get("target_date"):
			opp.expected_closing = data["target_date"]
		if data.get("our_reference") and not opp.get("custom_rfq_reference"):
			opp.custom_rfq_reference = data["our_reference"]
		opp.status = "Quotation"
		if review:
			opp.custom_needs_review = 1
		opp.custom_ai_summary = (
			f"{opp.get('custom_ai_summary') or ''}\n\n--- Our quote ({getdate(comm.communication_date)}) ---\n{summary}"
		).strip()
		opp.save(ignore_permissions=True)
		return opp, False, is_new_party

	opp = frappe.get_doc({
		"doctype": "Opportunity",
		"opportunity_from": party_type,
		"party_name": party,
		"title": data.get("title") or comm.subject,
		"company": settings.company,
		"territory": territory,
		"currency": currency,
		"opportunity_amount": data.get("overall_value") or 0,
		"expected_closing": data.get("target_date"),
		"status": "Quotation" if data["doc_type"] == "our_quotation" else "Open",
		"contact_email": data.get("customer_email"),
		"custom_rfq_type": "Our Quote" if data["doc_type"] == "our_quotation" else "Customer RFQ",
		"custom_needs_review": 1 if review else 0,
		"custom_ai_confidence": round(data.get("confidence", 0) * 100, 1),
		"custom_rfq_reference": data.get("our_reference"),
		"custom_ai_summary": summary,
		"custom_source_communication": comm.name,
		"custom_source_fingerprint": fingerprint,
		"items": _items(data),
	})
	opp.insert(ignore_permissions=True)
	return opp, True, is_new_party


def attach_files(opp, files: list[dict]) -> None:
	"""Attach each file to the Opportunity too (a new File row pointing at the same stored file)."""
	already = set(frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Opportunity", "attached_to_name": opp.name},
		pluck="file_url",
	))
	for f in files:
		if f["file_url"] in already:
			continue
		frappe.get_doc({
			"doctype": "File",
			"file_url": f["file_url"],
			"file_name": f["file_name"],
			"is_private": f.get("is_private", 1),
			"attached_to_doctype": "Opportunity",
			"attached_to_name": opp.name,
		}).insert(ignore_permissions=True)


def link_communication(comm, opp) -> None:
	"""Show the email on the Opportunity's timeline."""
	frappe.db.set_value(
		"Communication", comm.name,
		{"reference_doctype": "Opportunity", "reference_name": opp.name},
		update_modified=False,
	)
