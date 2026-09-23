# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 11: retention policy (spec 6).

Keep the newest N monthly backups; preserve year-end backups and anything an
administrator marked ``Keep Forever`` permanently. Everything else past the window
has its archive deleted (via the storage backend) and its ERP Backup record
removed. Only successful backups count toward the window — a Failed backup never
displaces a good one.
"""

import frappe

from playground.playground.erp_backup import constants, storage, utils


def apply_retention(settings=None):
	"""Enforce retention. Returns ``{"deleted": [...], "kept": int}``."""
	settings = settings or utils.get_settings()
	keep_n = int(settings.get("retention_monthly_count") or 12)
	backend = storage.get_backend(settings)

	# Newest first; only completed/verified backups are candidates for counting.
	candidates = frappe.get_all(
		constants.BACKUP_DOCTYPE,
		filters={"status": ["in", [constants.STATUS_COMPLETED, constants.STATUS_VERIFIED]]},
		fields=["name", "backup_type", "keep_forever", "archive_path", "started_at"],
		order_by="started_at desc",
	)

	deleted, monthly_seen = [], 0
	for row in candidates:
		if _is_protected(row, settings):
			continue
		# Monthly backups fill the rolling window; the oldest beyond it are pruned.
		monthly_seen += 1
		if monthly_seen <= keep_n:
			continue
		if _delete_backup(row, backend):
			deleted.append(row["name"])

	return {"deleted": deleted, "kept": len(candidates) - len(deleted)}


def _is_protected(row, settings):
	if row.get("keep_forever"):
		return True
	if row.get("backup_type") == constants.TYPE_YEAR_END and settings.get("keep_year_end_forever"):
		return True
	return False


def _delete_backup(row, backend):
	try:
		key = row.get("archive_path")
		if key:
			import os

			backend.delete(os.path.basename(key))
		frappe.delete_doc(constants.BACKUP_DOCTYPE, row["name"], ignore_permissions=True, force=True)
		return True
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"ERP Backup: retention delete {row.get('name')}")
		return False
