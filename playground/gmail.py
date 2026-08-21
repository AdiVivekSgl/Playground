"""
Gmail CRM sidebar API.

Endpoints called by the Gmail Chrome extension. One read path (get_context) that
identifies the customer/opportunity behind an open email thread, plus five write
actions. ERPNext stays the commercial system of record — financials (YTD sales,
last order, quotations) are read live from native doctypes and never duplicated
into the CRM layer.

get_context resolves EVERY external participant on the thread (not just the first
sender) and ignores internal staff addresses (default domain: frontec.co.in).

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

# Internal staff domains excluded from participant matching (comma-separated
# override accepted per-request via the internal_domains argument).
INTERNAL_DOMAINS_DEFAULT = "frontec.co.in"


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


def _primary_domain(emails):
	"""Most common non-public domain among the addresses (for the account header)."""
	counts = {}
	for e in emails:
		dom = e.split("@")[-1] if "@" in e else None
		if not dom or dom in PUBLIC_DOMAINS:
			continue
		counts[dom] = counts.get(dom, 0) + 1
	if counts:
		return max(counts, key=counts.get)
	# Fall back to the first address's domain, even if public.
	for e in emails:
		if "@" in e:
			return e.split("@")[-1]
	return None


def _resolve_contact(email):
	"""Map one email to a contact entry: known (linked to ERPNext/CRM) or not.

	Known ERPNext contacts get their CRM Contact mapping cached on the fly;
	unknown addresses are left for the explicit Create Contact action.
	"""
	email = _norm_email(email)
	entry = {
		"email": email,
		"contact_name": None,
		"crm_contact": None,
		"erpnext_contact": None,
		"customer": None,
		"customer_name": None,
		"erpnext_url": None,
		"known": False,
	}

	cc = frappe.db.get_value(
		"CRM Contact",
		{"email": email},
		["name", "contact_name", "erpnext_contact", "erpnext_customer"],
		as_dict=True,
	)
	if cc:
		entry.update(
			crm_contact=cc.name,
			contact_name=cc.contact_name,
			erpnext_contact=cc.erpnext_contact,
			customer=cc.erpnext_customer,
			known=True,
		)
	else:
		native_contact, native_customer = _find_native_customer_by_email(email)
		if native_contact or native_customer:
			mapping = _create_crm_contact_mapping(
				email=email,
				erpnext_contact=native_contact,
				erpnext_customer=native_customer,
			)
			entry.update(
				crm_contact=mapping["name"],
				erpnext_contact=native_contact,
				customer=native_customer,
				known=True,
			)
			if native_contact:
				parts = frappe.db.get_value(
					"Contact", native_contact, ["first_name", "last_name"], as_dict=True
				)
				if parts:
					entry["contact_name"] = " ".join(
						p for p in [parts.first_name, parts.last_name] if p
					)

	if entry["customer"]:
		entry["customer_name"] = frappe.db.get_value("Customer", entry["customer"], "customer_name")
	if entry["erpnext_contact"]:
		entry["erpnext_url"] = _erpnext_url("Contact", entry["erpnext_contact"])
	elif entry["crm_contact"]:
		entry["erpnext_url"] = _erpnext_url("CRM Contact", entry["crm_contact"])

	if not entry["contact_name"]:
		entry["contact_name"] = email.split("@")[0]
	return entry


def _thread_block(thread):
	if not thread:
		return None
	return {
		"gmail_thread_id": thread.gmail_thread_id,
		"crm_opportunity": thread.crm_opportunity,
		"status": thread.status,
	}


# ---------------------------------------------------------------------------
# read path
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_context(thread_id=None, sender=None, participants=None, internal_domains=None):
	"""Identify the CRM context behind an open Gmail thread.

	Resolves every external participant, ignoring internal staff addresses.
	Returns the account (customer or domain) on top plus one entry per contact.
	match_status: linked | matched | domain_only | unknown_contact.
	"""
	internal = {
		d.strip().lower()
		for d in (internal_domains or INTERNAL_DOMAINS_DEFAULT).split(",")
		if d.strip()
	}

	# Gather + clean addresses from participants and the (optional) sender.
	raw = []
	if participants:
		raw.extend(participants.split(","))
	if sender:
		raw.append(sender)

	emails = []
	seen = set()
	for e in raw:
		e = _norm_email(e)
		if not e or "@" not in e:
			continue
		if e.split("@")[-1] in internal:
			continue
		if e in seen:
			continue
		seen.add(e)
		emails.append(e)

	contacts = [_resolve_contact(e) for e in emails]
	domain = _primary_domain(emails)

	result = {
		"match_status": "unknown_contact",
		"account": {"type": "domain", "domain": domain, "customer": None},
		"contacts": contacts,
		"opportunities": [],
		"quotations": [],
		"activities": [],
		"domain_candidates": [],
		"thread": None,
	}

	# Thread fast-path: a previously linked conversation fixes the account.
	thread_doc = None
	linked_opp = None
	primary_customer = None
	if thread_id and frappe.db.exists("Gmail Thread", thread_id):
		thread_doc = frappe.get_doc("Gmail Thread", thread_id)
		if thread_doc.status == "Linked":
			primary_customer = thread_doc.customer
			linked_opp = thread_doc.crm_opportunity

	# Otherwise pick the most frequently referenced customer among participants.
	if not primary_customer:
		counts = {}
		for c in contacts:
			if c["customer"]:
				counts[c["customer"]] = counts.get(c["customer"], 0) + 1
		if counts:
			primary_customer = max(counts, key=counts.get)

	if primary_customer:
		is_linked = bool(thread_doc and thread_doc.status == "Linked")
		result["match_status"] = "linked" if is_linked else "matched"
		result["account"] = {
			"type": "customer",
			"domain": domain,
			"customer": _customer_block(primary_customer),
		}
		result["opportunities"] = _open_opportunities(customer=primary_customer)
		result["quotations"] = _quotations(primary_customer)
		result["activities"] = _recent_activities(
			customer=primary_customer, crm_opportunity=linked_opp
		)
	else:
		candidates = _domain_customers(domain)
		if candidates:
			result["match_status"] = "domain_only"
			result["domain_candidates"] = candidates

	# Keep the thread bridge fresh (store participants + best-known links).
	first_known = next((c for c in contacts if c["known"]), None)
	thread = _upsert_thread(
		thread_id,
		participants=",".join(emails) if emails else None,
		customer=primary_customer,
		contact=first_known["erpnext_contact"] if first_known else None,
		crm_contact=first_known["crm_contact"] if first_known else None,
	)
	result["thread"] = _thread_block(thread) if thread else None

	return result


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
