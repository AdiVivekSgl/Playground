# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Entry points: the Communication hook, the background job, and a manual re-run."""

import frappe
from frappe.core.utils import html2text

from playground.gmail import INTERNAL_DOMAINS_DEFAULT
from playground.playground.rfq_intake import crm_link, email_pdf, erp_mapping, extract, notify
from playground.playground.rfq_intake.utils import email_domain, fingerprint

SETTINGS_DOCTYPE = "RFQ Intake Settings"
JOB = "playground.playground.rfq_intake.handler.process"

# Inline images below this size are usually signature logos / tracking pixels.
MIN_IMAGE_BYTES = 15 * 1024
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


def _settings():
	return frappe.get_cached_doc(SETTINGS_DOCTYPE)


def on_email(doc, method=None):
	"""Communication.after_insert: queue received emails on the intake account."""
	if doc.communication_type != "Communication" or doc.sent_or_received != "Received":
		return
	settings = _settings()
	if not settings.enabled or doc.email_account != settings.email_account:
		return
	# enqueue_after_commit: attachments are saved after the Communication is inserted,
	# so wait until the whole inbound-mail transaction has committed.
	frappe.enqueue(
		JOB,
		queue="long",
		timeout=900,
		communication=doc.name,
		enqueue_after_commit=True,
		job_id=f"rfq_intake::{doc.name}",
		deduplicate=True,
	)


@frappe.whitelist()
def reprocess(communication: str):
	"""Re-run intake for an email, e.g. after fixing settings or an extraction failure."""
	frappe.only_for("System Manager")
	frappe.enqueue(JOB, queue="long", timeout=900, communication=communication)
	return "queued"


def _attachments(comm) -> tuple[list[dict], list[dict]]:
	"""Return (all attachments, meaningful attachments) on the Communication, excluding our own PDF."""
	own_pdf = email_pdf._safe_filename(comm)
	files = frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Communication", "attached_to_name": comm.name, "is_folder": 0},
		fields=["name", "file_name", "file_url", "is_private", "file_size", "content_hash"],
		order_by="creation asc",
	)
	files = [f for f in files if f.file_name != own_pdf]
	meaningful = [
		f for f in files
		if not ((f.file_name or "").lower().endswith(IMAGE_EXTS) and (f.file_size or 0) < MIN_IMAGE_BYTES)
	]
	return files, meaningful


def _own_domains(settings, comm) -> set[str]:
	"""Our staff domains: never treated as the customer."""
	raw = settings.get("internal_domains") or INTERNAL_DOMAINS_DEFAULT
	domains = {d.strip().lower() for d in raw.split(",") if d.strip()}
	account_email = frappe.db.get_value("Email Account", settings.email_account, "email_id")
	for addr in (account_email, comm.sender):
		d = email_domain(addr)
		if d:
			domains.add(d)
	return domains


def process(communication: str):
	settings = _settings()
	comm = frappe.get_doc("Communication", communication)
	files, meaningful = _attachments(comm)
	fp = fingerprint(comm.subject, [f.content_hash for f in meaningful], html2text(comm.content or ""))

	# Same RFQ forwarded again: just link the new email to the existing Opportunity.
	dup = frappe.db.get_value("Opportunity", {"custom_source_fingerprint": fp}, "name")
	if dup:
		opp = frappe.get_doc("Opportunity", dup)
		erp_mapping.link_communication(comm, opp)
		opp.add_comment("Comment", f"Duplicate forward received ({comm.name}); linked, not reprocessed.")
		return

	pdf = email_pdf.attach_email_pdf(comm, files)

	own_domains = _own_domains(settings, comm)
	territories = frappe.get_all("Territory", filters={"is_group": 0}, pluck="name", order_by="name")
	try:
		data = extract.extract(comm, meaningful, settings, territories, own_domains)
	except Exception:
		frappe.log_error(title=f"RFQ Intake: extraction failed for {comm.name}")
		comm.add_comment("Comment", "RFQ Intake: AI extraction failed; see Error Log. Email PDF saved. Use Reprocess to retry.")
		return

	if data["doc_type"] == "other":
		comm.add_comment("Comment", f"RFQ Intake: not an RFQ or quotation, skipped.\n{data.get('notes') or ''}")
		return

	opp, created, _is_new_party = erp_mapping.upsert_opportunity(data, comm, settings, own_domains, fp)
	erp_mapping.attach_files(opp, [pdf, *files])
	erp_mapping.link_communication(comm, opp)
	if data.get("_skipped_attachments"):
		opp.add_comment("Comment", "Not read by AI: " + ", ".join(data["_skipped_attachments"]))

	crm_name = None
	frappe.db.savepoint("rfq_intake_crm")
	try:
		crm_name = crm_link.sync(opp, data, comm)
	except Exception:
		# The native Opportunity is the record of truth; a CRM-layer failure must not undo it.
		frappe.db.rollback(save_point="rfq_intake_crm")
		frappe.log_error(title=f"RFQ Intake: CRM Opportunity sync failed for {opp.name}")

	frappe.db.commit()

	if settings.send_summary_reply:
		try:
			notify.send_summary(comm, opp, data, created, crm_name)
		except Exception:
			frappe.log_error(title=f"RFQ Intake: summary reply failed for {comm.name}")
