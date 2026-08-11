"""
Gmail CRM sidebar API.

Endpoints called by the Gmail Chrome extension. One read path (get_context) that
identifies the customer/opportunity behind an open email thread, plus five write
actions. ERPNext stays the commercial system of record — financials (YTD sales,
last order, quotations) are read live from native doctypes and never duplicated
into the CRM layer.

Dotted path: playground.gmail.<method>  (e.g. /api/method/playground.gmail.get_context)
"""

from urllib.parse import quote

import frappe
from frappe.utils import get_url, getdate, nowdate

PUBLIC_DOMAINS = {
	"gmail.com",
	"googlemail.com",
	"yahoo.com",
	"outlook.com",
	"hotmail.com",
	"live.com",
	"icloud.com",
	"aol.com",
	"protonmail.com",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _norm_email(email):
	return (email or "").strip().lower()


def _erpnext_url(doctype, name):
	if not name:
		return None
	slug = frappe.scrub(doctype).replace("_", "-")
	return f"{get_url()}/app/{slug}/{quote(str(name))}"


def _customer_block(customer):
	"""Identity + live financials read from native ERPNext."""
	if not customer:
		return None

	customer_name = frappe.db.get_value("Customer", customer, "customer_name") or customer

	# YTD sales = submitted Sales Invoices inside the current fiscal year.
	ytd_sales = 0
	try:
		fy = frappe.get_cached_value(
			"Fiscal Year",
			frappe.defaults.get_user_default("fiscal_year") or _current_fiscal_year(),
			["year_start_date", "year_end_date"],
			as_dict=True,
		)
	except Exception:
		fy = None
	if fy:
		rows = frappe.get_all(
			"Sales Invoice",
			filters={
				"customer": customer,
				"docstatus": 1,
				"posting_date": ["between", [fy.year_start_date, fy.year_end_date]],
			},
			fields=["sum(base_grand_total) as total"],
		)
		ytd_sales = (rows[0].total if rows else 0) or 0

	last_order = frappe.get_all(
		"Sales Order",
		filters={"customer": customer, "docstatus": 1},
		fields=["transaction_date"],
		order_by="transaction_date desc",
		limit=1,
	)
	last_order_date = last_order[0].transaction_date if last_order else None

	return {
		"name": customer,
		"customer_name": customer_name,
		"ytd_sales": ytd_sales,
		"last_order_date": last_order_date,
		"erpnext_url": _erpnext_url("Customer", customer),
	}


def _current_fiscal_year():
	fy = frappe.get_all(
		"Fiscal Year",
		filters={
			"year_start_date": ["<=", nowdate()],
			"year_end_date": [">=", nowdate()],
		},
		fields=["name"],
		limit=1,
	)
	return fy[0].name if fy else None


def _open_opportunities(customer=None, crm_contact=None):
	filters = {"status": "Open"}
	if customer:
		filters["customer"] = customer
	elif crm_contact:
		filters["crm_contact"] = crm_contact
	else:
		return []
	opps = frappe.get_all(
		"CRM Opportunity",
		filters=filters,
		fields=[
			"name",
			"opportunity_name",
			"value",
			"stage",
			"next_action",
			"next_action_date",
		],
		order_by="modified desc",
	)
	for o in opps:
		o["erpnext_url"] = _erpnext_url("CRM Opportunity", o["name"])
	return opps


def _quotations(customer):
	if not customer:
		return []
	quos = frappe.get_all(
		"Quotation",
		filters={"party_name": customer, "quotation_to": "Customer"},
		fields=["name", "status", "grand_total", "transaction_date"],
		order_by="transaction_date desc",
		limit=5,
	)
	for q in quos:
		q["erpnext_url"] = _erpnext_url("Quotation", q["name"])
	return quos


def _recent_activities(customer=None, crm_opportunity=None, limit=10):
	filters = {}
	if crm_opportunity:
		filters["crm_opportunity"] = crm_opportunity
	elif customer:
		filters["customer"] = customer
	else:
		return []
	return frappe.get_all(
		"CRM Activity",
		filters=filters,
		fields=["activity_type", "activity_datetime", "description", "user"],
		order_by="activity_datetime desc",
		limit=limit,
	)


def _upsert_thread(thread_id, subject=None, participants=None, **links):
	"""Create or update the Gmail Thread bridge record."""
	if not thread_id:
		return None
	name = frappe.db.exists("Gmail Thread", thread_id)
	if name:
		doc = frappe.get_doc("Gmail Thread", name)
	else:
		doc = frappe.new_doc("Gmail Thread")
		doc.gmail_thread_id = thread_id

	if subject:
		doc.subject = subject
	if participants:
		doc.participants = participants
	for field, value in links.items():
		if value:
			setattr(doc, field, value)
	doc.last_synced = frappe.utils.now_datetime()
	doc.save(ignore_permissions=True)
	return doc


def _find_native_customer_by_email(email):
	"""Resolve a native ERPNext Contact -> Customer via Contact Email + Dynamic Link."""
	contact_names = frappe.get_all(
		"Contact Email",
		filters={"email_id": email},
		fields=["parent"],
		limit=1,
	)
	if not contact_names:
		return None, None
	contact = contact_names[0].parent
	link = frappe.get_all(
		"Dynamic Link",
		filters={
			"parenttype": "Contact",
			"parent": contact,
			"link_doctype": "Customer",
		},
		fields=["link_name"],
		limit=1,
	)
	customer = link[0].link_name if link else None
	return contact, customer


def _domain_customers(domain):
	"""Company-level fallback: customers reachable via a non-public email domain."""
	if not domain or domain in PUBLIC_DOMAINS:
		return []
	contacts = frappe.get_all(
		"Contact Email",
		filters={"email_id": ["like", f"%@{domain}"]},
		fields=["parent"],
		limit=20,
	)
	seen = set()
	customers = []
	for c in contacts:
		link = frappe.get_all(
			"Dynamic Link",
			filters={
				"parenttype": "Contact",
				"parent": c.parent,
				"link_doctype": "Customer",
			},
			fields=["link_name"],
			limit=1,
		)
		if link and link[0].link_name not in seen:
			seen.add(link[0].link_name)
			customers.append(
				{
					"name": link[0].link_name,
					"customer_name": frappe.db.get_value("Customer", link[0].link_name, "customer_name"),
					"erpnext_url": _erpnext_url("Customer", link[0].link_name),
				}
			)
	return customers


# ---------------------------------------------------------------------------
# read path
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_context(thread_id=None, sender=None, participants=None):
	"""Identify the CRM context behind an open Gmail thread.

	Returns match_status one of: linked | matched | domain_only | unknown_contact.
	"""
	sender = _norm_email(sender)
	domain = sender.split("@")[-1] if "@" in sender else None

	result = {
		"match_status": "unknown_contact",
		"customer": None,
		"contact": None,
		"opportunities": [],
		"quotations": [],
		"activities": [],
		"thread": None,
		"domain_candidates": [],
	}

	# 1) Fast path — thread recognition.
	if thread_id and frappe.db.exists("Gmail Thread", thread_id):
		thread = frappe.get_doc("Gmail Thread", thread_id)
		if thread.status == "Linked":
			result["match_status"] = "linked"
			result["customer"] = _customer_block(thread.customer)
			result["contact"] = _contact_block(thread.contact, sender)
			result["opportunities"] = _open_opportunities(
				customer=thread.customer, crm_contact=thread.crm_contact
			)
			result["quotations"] = _quotations(thread.customer)
			result["activities"] = _recent_activities(
				customer=thread.customer, crm_opportunity=thread.crm_opportunity
			)
			result["thread"] = _thread_block(thread)
			return result

	# 2) Contact match — CRM Contact by email.
	crm_contact = None
	if sender:
		crm_contact = frappe.db.get_value(
			"CRM Contact",
			{"email": sender},
			["name", "erpnext_customer", "erpnext_contact"],
			as_dict=True,
		)

	# 3) ERPNext fallback — resolve native Contact -> Customer, then map it.
	if not crm_contact and sender:
		native_contact, native_customer = _find_native_customer_by_email(sender)
		if native_contact or native_customer:
			crm_contact = _create_crm_contact_mapping(
				email=sender,
				erpnext_contact=native_contact,
				erpnext_customer=native_customer,
			)

	if crm_contact:
		customer = crm_contact.get("erpnext_customer")
		contact = crm_contact.get("erpnext_contact")
		thread = _upsert_thread(
			thread_id,
			participants=participants,
			crm_contact=crm_contact.get("name"),
			customer=customer,
			contact=contact,
		)
		result["match_status"] = "matched"
		result["customer"] = _customer_block(customer)
		result["contact"] = _contact_block(contact, sender)
		result["opportunities"] = _open_opportunities(
			customer=customer, crm_contact=crm_contact.get("name")
		)
		result["quotations"] = _quotations(customer)
		result["activities"] = _recent_activities(customer=customer)
		result["thread"] = _thread_block(thread) if thread else None
		return result

	# 4) Domain fallback — surface the account, never auto-link.
	candidates = _domain_customers(domain)
	if candidates:
		result["match_status"] = "domain_only"
		result["domain_candidates"] = candidates
		return result

	# 5) Unknown.
	return result


def _contact_block(contact, sender=None):
	if not contact:
		return {"name": None, "contact_name": None, "email": sender, "erpnext_url": None}
	contact_name = frappe.db.get_value("Contact", contact, "first_name") or contact
	return {
		"name": contact,
		"contact_name": contact_name,
		"email": sender,
		"erpnext_url": _erpnext_url("Contact", contact),
	}


def _thread_block(thread):
	if not thread:
		return None
	return {
		"gmail_thread_id": thread.gmail_thread_id,
		"crm_opportunity": thread.crm_opportunity,
		"status": thread.status,
	}


# ---------------------------------------------------------------------------
# write actions (the 5 MVP actions)
# ---------------------------------------------------------------------------

def _create_crm_contact_mapping(email, contact_name=None, erpnext_customer=None, erpnext_contact=None):
	email = _norm_email(email)
	name = frappe.db.exists("CRM Contact", email)
	if name:
		doc = frappe.get_doc("CRM Contact", name)
	else:
		doc = frappe.new_doc("CRM Contact")
		doc.email = email
	if contact_name:
		doc.contact_name = contact_name
	if erpnext_customer:
		doc.erpnext_customer = erpnext_customer
	if erpnext_contact:
		doc.erpnext_contact = erpnext_contact
	doc.sales_owner = doc.sales_owner or frappe.session.user
	doc.save(ignore_permissions=True)
	return {
		"name": doc.name,
		"erpnext_customer": doc.erpnext_customer,
		"erpnext_contact": doc.erpnext_contact,
	}


@frappe.whitelist()
def create_contact(email, contact_name=None, customer=None):
	"""Action 1 — associate a Gmail identity with the CRM (and optionally a customer)."""
	mapping = _create_crm_contact_mapping(
		email=email, contact_name=contact_name, erpnext_customer=customer
	)
	return {"crm_contact": mapping["name"], "erpnext_url": _erpnext_url("CRM Contact", mapping["name"])}


@frappe.whitelist()
def create_opportunity(
	opportunity_name,
	customer=None,
	contact=None,
	value=None,
	expected_closing=None,
	sales_owner=None,
	thread_id=None,
):
	"""Action 2 — create a CRM Opportunity and bind the Gmail thread to it."""
	crm_contact = None
	if thread_id and frappe.db.exists("Gmail Thread", thread_id):
		crm_contact = frappe.db.get_value("Gmail Thread", thread_id, "crm_contact")

	opp = frappe.new_doc("CRM Opportunity")
	opp.opportunity_name = opportunity_name
	opp.customer = customer
	opp.contact = contact
	opp.crm_contact = crm_contact
	opp.value = value
	opp.expected_closing = getdate(expected_closing) if expected_closing else None
	opp.sales_owner = sales_owner or frappe.session.user
	opp.source = "Gmail"
	opp.insert(ignore_permissions=True)

	# Bind the thread so a future reply re-opens straight to this opportunity.
	if thread_id:
		_upsert_thread(
			thread_id,
			crm_opportunity=opp.name,
			customer=customer,
			contact=contact,
			crm_contact=crm_contact,
		)

	# Log the creation on the timeline.
	_log_activity(
		activity_type="Note",
		description=f"Opportunity {opp.name} created from Gmail.",
		crm_opportunity=opp.name,
		customer=customer,
		contact=contact,
		thread_id=thread_id,
	)

	return {"name": opp.name, "erpnext_url": _erpnext_url("CRM Opportunity", opp.name)}


def _log_activity(
	activity_type,
	description,
	crm_opportunity=None,
	customer=None,
	contact=None,
	thread_id=None,
	follow_up_date=None,
):
	act = frappe.new_doc("CRM Activity")
	act.activity_type = activity_type
	act.description = description
	act.crm_opportunity = crm_opportunity
	act.customer = customer
	act.contact = contact
	act.gmail_thread_id = thread_id
	act.user = frappe.session.user
	if follow_up_date:
		act.follow_up_date = getdate(follow_up_date)
		act.follow_up_status = "Open"
	act.insert(ignore_permissions=True)
	return act


def _thread_defaults(thread_id):
	"""Pull customer/contact/opportunity off the bound thread, if any."""
	if thread_id and frappe.db.exists("Gmail Thread", thread_id):
		return frappe.db.get_value(
			"Gmail Thread",
			thread_id,
			["customer", "contact", "crm_opportunity"],
			as_dict=True,
		)
	return frappe._dict()


@frappe.whitelist()
def add_note(description, thread_id=None, crm_opportunity=None):
	"""Action 3 — add a free-text note to the timeline."""
	defaults = _thread_defaults(thread_id)
	act = _log_activity(
		activity_type="Note",
		description=description,
		crm_opportunity=crm_opportunity or defaults.get("crm_opportunity"),
		customer=defaults.get("customer"),
		contact=defaults.get("contact"),
		thread_id=thread_id,
	)
	return {"name": act.name}


@frappe.whitelist()
def add_follow_up(follow_up_date, description, thread_id=None, crm_opportunity=None):
	"""Action 4 — schedule a follow-up (a CRM Activity of type Follow-up)."""
	defaults = _thread_defaults(thread_id)
	act = _log_activity(
		activity_type="Follow-up",
		description=description,
		crm_opportunity=crm_opportunity or defaults.get("crm_opportunity"),
		customer=defaults.get("customer"),
		contact=defaults.get("contact"),
		thread_id=thread_id,
		follow_up_date=follow_up_date,
	)
	return {"name": act.name}


@frappe.whitelist()
def link_thread(thread_id, crm_opportunity):
	"""Action 5 — bind an existing Gmail thread to an opportunity."""
	opp = frappe.db.get_value(
		"CRM Opportunity", crm_opportunity, ["customer", "contact", "crm_contact"], as_dict=True
	)
	thread = _upsert_thread(
		thread_id,
		crm_opportunity=crm_opportunity,
		customer=opp.customer if opp else None,
		contact=opp.contact if opp else None,
		crm_contact=opp.crm_contact if opp else None,
	)
	return {"gmail_thread_id": thread.gmail_thread_id, "status": thread.status}
