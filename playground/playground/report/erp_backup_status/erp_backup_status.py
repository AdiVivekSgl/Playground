# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""ERP Backup Status dashboard (spec 12).

A one-glance answer to the question in the spec's Definition of Done: when was my
last successful backup, can I download it, was it verified, and how big was it?
Failed rows are highlighted (see the .js formatter) and a prominent banner warns
when there has been no successful backup within the expected period.
"""

import frappe
from frappe import _
from frappe.utils import add_months, cint, get_first_day, getdate

from playground.playground.erp_backup import constants, utils


def execute(filters=None):
	filters = frappe._dict(filters or {})
	frappe.only_for(constants.ALLOWED_ROLES)

	columns = get_columns()
	rows = get_rows(filters)
	message = get_banner()
	report_summary = get_summary()
	return columns, rows, message, None, report_summary


def get_columns():
	return [
		{"label": _("Backup"), "fieldname": "backup", "fieldtype": "Link", "options": "ERP Backup", "width": 150},
		{"label": _("Date"), "fieldname": "backup_date", "fieldtype": "Datetime", "width": 170},
		{"label": _("Type"), "fieldname": "backup_type", "fieldtype": "Data", "width": 90},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
		{"label": _("Verified"), "fieldname": "verification", "fieldtype": "Data", "width": 90},
		{"label": _("Size"), "fieldname": "size", "fieldtype": "Data", "width": 100},
		{"label": _("Action"), "fieldname": "download", "fieldtype": "HTML", "width": 120},
	]


def get_rows(filters):
	conditions = {}
	if filters.get("status"):
		conditions["status"] = filters.status
	if filters.get("from_date") and filters.get("to_date"):
		conditions["started_at"] = ["between", [filters.from_date, filters.to_date]]

	records = frappe.get_all(
		constants.BACKUP_DOCTYPE,
		filters=conditions,
		fields=[
			"name", "started_at", "creation", "backup_type", "status",
			"verification_status", "archive_size", "archive_path",
		],
		order_by="creation desc",
		limit_page_length=cint(filters.get("limit")) or 200,
	)

	rows = []
	for rec in records:
		rows.append({
			"backup": rec.name,
			"backup_date": rec.started_at or rec.creation,
			"backup_type": rec.backup_type,
			"status": rec.status,
			"verification": rec.verification_status,
			"size": utils.human_size(rec.archive_size),
			"download": _download_link(rec),
			# raw flag consumed by the .js formatter to colour failed rows.
			"_failed": 1 if rec.status == constants.STATUS_FAILED else 0,
		})
	return rows


def _download_link(rec):
	if not rec.archive_path or rec.status not in (constants.STATUS_COMPLETED, constants.STATUS_VERIFIED):
		return ""
	from urllib.parse import quote

	url = (
		"/api/method/playground.playground.erp_backup.api.download_backup?name="
		+ quote(rec.name)
	)
	return f'<a class="btn btn-xs btn-default" href="{url}" target="_blank">{_("Download")}</a>'


def get_banner():
	"""HTML warning shown above the table when no recent successful backup exists."""
	if _has_recent_success():
		return None
	return (
		'<div class="text-danger" style="font-weight:bold;padding:8px 0;">'
		+ _("⚠ No verified backup in the last 35 days. Check ERP Backup Settings and run a backup now.")
		+ "</div>"
	)


def get_summary():
	last_success = _last_success()
	summary = [
		{
			"label": _("Last Backup"),
			"value": frappe.utils.format_datetime(last_success.started_at) if last_success else _("Never"),
			"indicator": "Green" if last_success else "Red",
			"datatype": "Data",
		},
		{
			"label": _("Next Backup"),
			"value": _next_backup_label(),
			"datatype": "Data",
			"indicator": "Blue",
		},
		{
			"label": _("Latest Size"),
			"value": utils.human_size(last_success.archive_size) if last_success else "—",
			"datatype": "Data",
		},
		{
			"label": _("Last Verification"),
			"value": (last_success.verification_status if last_success else _("None")),
			"indicator": "Green" if last_success else "Red",
			"datatype": "Data",
		},
	]
	return summary


def _last_success():
	rows = frappe.get_all(
		constants.BACKUP_DOCTYPE,
		filters={"status": constants.STATUS_VERIFIED},
		fields=["name", "started_at", "archive_size", "verification_status"],
		order_by="started_at desc",
		limit=1,
	)
	return rows[0] if rows else None


def _has_recent_success(days=35):
	cutoff = frappe.utils.add_days(frappe.utils.nowdate(), -days)
	return bool(
		frappe.get_all(
			constants.BACKUP_DOCTYPE,
			filters={"status": constants.STATUS_VERIFIED, "started_at": [">=", cutoff]},
			limit=1,
		)
	)


def _next_backup_label():
	settings = utils.get_settings()
	if not settings.get("enabled"):
		return _("Scheduled backups disabled")
	day = cint(settings.get("day_of_month") or 1)
	hour = cint(settings.get("hour") or 2)
	today = getdate()
	# Next occurrence of `day` at/after today; roll to next month if past.
	candidate = get_first_day(today).replace(day=min(day, 28))
	if candidate < today:
		candidate = add_months(candidate, 1)
	return f"{frappe.utils.formatdate(candidate)} {hour:02d}:00"
