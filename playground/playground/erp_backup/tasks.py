# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Scheduler entry point (spec 7).

Wired as an HOURLY cron in ``hooks.py``. The task itself enforces the configurable
schedule and the ship-disabled default: it no-ops unless backups are enabled AND
today's day-of-month and hour match the configured schedule AND no backup has
already run this calendar month. When it fires it inserts a Queued record and
enqueues the SAME pipeline the manual button uses.
"""

import frappe
from frappe.utils import cint, get_first_day, now_datetime

from playground.playground.erp_backup import constants, utils


def scheduled_backup_check():
	"""Fire the monthly backup when the configured moment arrives; else no-op."""
	settings = utils.get_settings()
	if not settings.get("enabled"):
		return

	now = now_datetime()
	if now.day != cint(settings.get("day_of_month") or 1):
		return
	if now.hour != cint(settings.get("hour") or 2):
		return
	if _already_ran_this_month(now):
		return

	backup_type = (
		constants.TYPE_YEAR_END
		if now.month == cint(settings.get("year_end_month") or 12)
		else constants.TYPE_MONTHLY
	)

	doc = frappe.new_doc(constants.BACKUP_DOCTYPE)
	doc.backup_type = backup_type
	doc.description = f"Scheduled {backup_type.lower()} backup"
	doc.status = constants.STATUS_QUEUED
	doc.insert(ignore_permissions=True)
	frappe.db.commit()

	frappe.enqueue(
		"playground.playground.erp_backup.pipeline.run_backup",
		queue="long",
		timeout=constants.BACKUP_TIMEOUT,
		backup_name=doc.name,
	)


def _already_ran_this_month(now):
	"""True if a scheduled backup this month is queued/running/done (dedup guard)."""
	month_start = get_first_day(now)
	return bool(
		frappe.get_all(
			constants.BACKUP_DOCTYPE,
			filters={
				"backup_type": ["in", [constants.TYPE_MONTHLY, constants.TYPE_YEAR_END]],
				"creation": [">=", month_start],
				"status": ["in", [
					constants.STATUS_QUEUED,
					constants.STATUS_RUNNING,
					constants.STATUS_COMPLETED,
					constants.STATUS_VERIFIED,
				]],
			},
			limit=1,
		)
	)
