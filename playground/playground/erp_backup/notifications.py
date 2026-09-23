# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 12: administrator notifications (spec 13).

Success mail is sent ONLY after verification succeeds (the pipeline guarantees this
by calling :func:`notify_success` only on the Verified path). Failures send an
URGENT mail naming the failing stage. Both also drop an in-app Notification Log
entry for System Managers. Notifications must never raise — a delivery problem is
logged, not allowed to flip a good backup to Failed.
"""

import frappe

from playground.playground.erp_backup import constants, utils


def notify_success(doc, settings=None):
	settings = settings or utils.get_settings()
	subject = f"ERP Monthly Backup Completed — {_date(doc)}"
	body = (
		f"ERP Monthly Backup Completed\n\n"
		f"Backup: {doc.name} ({doc.get('backup_type')})\n"
		f"Status: {doc.get('status')}\n\n"
		f"Database: {utils.human_size(doc.get('database_size'))}\n"
		f"Files: {utils.human_size(doc.get('files_size'))}\n"
		f"Archive: {utils.human_size(doc.get('archive_size'))}\n\n"
		f"Checksum: {doc.get('checksum')}\n\n"
		f"The backup is available for download from ERP Backup."
	)
	_send(settings, subject, body)
	_notification_log(subject, doc)


def notify_failure(doc, stage, error, settings=None):
	settings = settings or utils.get_settings()
	subject = f"URGENT: ERP Backup Failed — {_date(doc)}"
	body = (
		f"URGENT: ERP Backup Failed\n\n"
		f"Backup: {doc.name}\n"
		f"Stage: {stage}\n\n"
		f"Error:\n{_trim(error)}\n\n"
		f"Please investigate immediately."
	)
	_send(settings, subject, body)
	_notification_log(subject, doc)


# --- helpers ---------------------------------------------------------------

def _send(settings, subject, body):
	recipients = _recipients(settings)
	if not recipients:
		return
	try:
		frappe.sendmail(recipients=recipients, subject=subject, message=body.replace("\n", "<br>"))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ERP Backup: sending notification email failed")


def _notification_log(subject, doc):
	try:
		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

		for user in _system_managers():
			enqueue_create_notification(
				user,
				{
					"type": "Alert",
					"document_type": constants.BACKUP_DOCTYPE,
					"document_name": doc.name,
					"subject": subject,
					"from_user": frappe.session.user,
				},
			)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ERP Backup: creating notification log failed")


def _recipients(settings):
	raw = (settings.get("notification_recipients") or "").strip()
	if raw:
		emails = [e.strip() for e in raw.replace(",", "\n").splitlines() if e.strip()]
		if emails:
			return emails
	# Fall back to every enabled System Manager's email.
	return frappe.get_all(
		"User",
		filters={"enabled": 1, "name": ["in", _system_managers()]},
		pluck="email",
	)


def _system_managers():
	return [
		u for u in frappe.get_all(
			"Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent"
		)
		if u not in ("Administrator", "Guest")
	] or ["Administrator"]


def _date(doc):
	return frappe.utils.formatdate(doc.get("started_at") or frappe.utils.nowdate())


def _trim(text, limit=2000):
	text = str(text or "")
	return text if len(text) <= limit else text[:limit] + "\n... (truncated)"
