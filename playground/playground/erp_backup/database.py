# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 1: the authoritative database dump (and the raw file tars).

We do NOT reimplement mysqldump. Frappe's own ``new_backup`` produces exactly the
artifacts ``bench restore`` consumes — ``database.sql.gz`` plus the public/private
file tars — and that dump is the single source of truth for ERP restoration
(spec 1A, 17, 18). This stage runs it once and copies ``database.sql.gz`` into the
staging ``DATABASE/`` directory; the file tars it produced are handed to the file
stage so ``new_backup`` runs only once.
"""

import os
import shutil

import frappe

from playground.playground.erp_backup import constants


def dump_database(staging_dir):
	"""Run Frappe's native backup and stage the database dump.

	Returns a dict with the staged DB path + size and the raw file-tar paths that
	the file stage will consume::

	    {"db_path": "<staging>/DATABASE/database.sql.gz", "db_size": int,
	     "files_tar": "<...>-files.tar" | None,
	     "private_tar": "<...>-private-files.tar" | None}
	"""
	backup = _run_frappe_backup()

	src_db = backup.backup_path_db
	if not src_db or not os.path.isfile(src_db):
		raise BackupStageError(frappe._("Frappe backup did not produce a database dump."))
	if os.path.getsize(src_db) == 0:
		raise BackupStageError(frappe._("Database dump is empty."))

	db_dir = os.path.join(staging_dir, constants.DIR_DATABASE)
	os.makedirs(db_dir, exist_ok=True)
	dest_db = os.path.join(db_dir, constants.DB_DUMP_NAME)
	# Move rather than copy so we don't keep a second multi-GB copy on disk.
	shutil.move(src_db, dest_db)

	return {
		"db_path": dest_db,
		"db_size": os.path.getsize(dest_db),
		"files_tar": backup.backup_path_files,
		"private_tar": backup.backup_path_private_files,
	}


def _run_frappe_backup():
	"""Thin wrapper around ``frappe.utils.backups.new_backup`` (isolated for tests)."""
	from frappe.utils.backups import new_backup

	# ignore_files=False -> also produce the public/private file tars in one pass.
	# force=True -> always create a fresh dump (never reuse a recent one).
	return new_backup(ignore_files=False, force=True, verbose=False)


class BackupStageError(frappe.ValidationError):
	"""Raised when a backup stage cannot complete; surfaces as a Failed backup."""
