# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Whitelisted server API for the ERP Backup module.

All endpoints are gated to System Manager / Backup Manager (spec 8, 15). The
heavy work always runs in a background job so the browser request returns
immediately (spec 14, 16) — ``create_backup_now`` inserts a Queued record and
enqueues the pipeline; it never runs the backup inline.
"""

import frappe
from frappe.utils import cint

from playground.playground.erp_backup import constants, download


@frappe.whitelist()
def create_backup_now(backup_type=None, description=None, keep_forever=0):
	"""Manually trigger a backup. Returns the queued record — does not block.

	Same pipeline as the scheduled backup; only the trigger differs (spec 14).
	"""
	frappe.only_for(constants.ALLOWED_ROLES)

	doc = frappe.new_doc(constants.BACKUP_DOCTYPE)
	doc.backup_type = backup_type or constants.TYPE_MANUAL
	doc.description = description
	doc.keep_forever = cint(keep_forever)
	doc.status = constants.STATUS_QUEUED
	doc.insert()  # permission enforced by DocType perms
	frappe.db.commit()

	frappe.enqueue(
		"playground.playground.erp_backup.pipeline.run_backup",
		queue="long",
		timeout=constants.BACKUP_TIMEOUT,
		backup_name=doc.name,
		user=frappe.session.user,
	)
	return {"name": doc.name, "status": doc.status, "queued": True}


@frappe.whitelist()
def download_backup(name):
	"""Stream a completed backup archive to an authorised user (spec 8)."""
	return download.stream_backup(name)


@frappe.whitelist()
def get_dashboard_data():
	"""Summary for the dashboard/report header (spec 12)."""
	frappe.only_for(constants.ALLOWED_ROLES)

	last_success = frappe.get_all(
		constants.BACKUP_DOCTYPE,
		filters={"status": constants.STATUS_VERIFIED},
		fields=["name", "started_at", "archive_size", "verification_status", "backup_type"],
		order_by="started_at desc",
		limit=1,
	)
	last_any = frappe.get_all(
		constants.BACKUP_DOCTYPE,
		fields=["name", "status", "started_at"],
		order_by="creation desc",
		limit=1,
	)
	return {
		"last_success": last_success[0] if last_success else None,
		"last_any": last_any[0] if last_any else None,
		"has_recent_success": _has_recent_success(),
	}


def _has_recent_success(days=35):
	cutoff = frappe.utils.add_days(frappe.utils.nowdate(), -days)
	return bool(
		frappe.get_all(
			constants.BACKUP_DOCTYPE,
			filters={"status": constants.STATUS_VERIFIED, "started_at": [">=", cutoff]},
			limit=1,
		)
	)
