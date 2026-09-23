# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Reply to the person who forwarded the email with what was extracted."""

import frappe
from frappe.utils import escape_html, fmt_money, get_url_to_form


def send_summary(comm, opp, data: dict, created: bool, crm_opportunity: str | None = None) -> None:
	link = get_url_to_form("Opportunity", opp.name)
	value = fmt_money(opp.opportunity_amount, currency=opp.currency) if data.get("overall_value") is not None else "not stated"
	rows = [
		("Opportunity", f'<a href="{link}">{escape_html(opp.name)}</a> ({"created" if created else "updated"})'),
		("Title", escape_html(opp.title or "")),
		("Customer", f"{escape_html(opp.party_name)} ({opp.opportunity_from})"),
		("Territory", escape_html(opp.territory or "")),
		("Target date", escape_html(str(opp.expected_closing or "not stated"))),
		("Overall value", escape_html(value)),
		("Confidence", f"{round(data.get('confidence', 0) * 100)}%"),
	]
	if crm_opportunity:
		crm_link = get_url_to_form("CRM Opportunity", crm_opportunity)
		rows.append(("CRM Opportunity", f'<a href="{crm_link}">{escape_html(crm_opportunity)}</a>'))
	table = "".join(f"<tr><td style='color:#666;padding-right:12px'>{k}</td><td>{v}</td></tr>" for k, v in rows)
	review = "<p><b>⚠ Flagged for review</b> - please check the fields above.</p>" if opp.get("custom_needs_review") else ""

	frappe.sendmail(
		recipients=[comm.sender],
		subject=f"Logged: {opp.title}",
		message=f"<p>Your forwarded email was logged in ERPNext.</p><table>{table}</table>{review}",
		reference_doctype="Opportunity",
		reference_name=opp.name,
		in_reply_to=comm.message_id,
	)
