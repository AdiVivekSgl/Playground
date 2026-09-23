# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""The backup pipeline — the single entry point for scheduled and manual backups.

``run_backup(backup_name)`` drives every stage in order (spec 7):

    Start -> Database -> Files -> Data export -> Metadata -> Manifest ->
    Archive -> Checksum -> Verify -> mark Verified -> Retention -> Notify

The rules that matter (spec 7, 13):
  * The ERP Backup record reflects progress live (``current_stage`` + ``status``),
    committed between stages so the dashboard is truthful even mid-run.
  * ANY stage failure marks the backup ``Failed``, records the stage + error, sends
    the URGENT failure mail, and stops. The archive being created is never enough.
  * ``Verified`` is set ONLY after :mod:`verify` passes; the success mail is sent
    only on that path.

Both the scheduler and the "Create Backup Now" button enqueue this same function,
so manual and automatic backups are byte-for-byte the same process (spec 14).
"""

import os
import shutil

import frappe
from frappe.utils import now_datetime

from playground.playground.erp_backup import (
	archive as archive_mod,
	constants,
	data_export,
	database,
	files,
	manifest as manifest_mod,
	metadata,
	notifications,
	retention,
	storage,
	utils,
	verify,
)


def run_backup(backup_name, user=None):
	"""Execute the full pipeline for an existing ERP Backup record.

	Designed to run inside a background job (``frappe.enqueue``). Never raises out
	of the job on a backup failure — it records the failure on the record instead.
	"""
	# Re-assert authorization inside the job (the enqueued path bypassed the
	# endpoint's own check), mirroring the app's google_docs pattern.
	if user and user != frappe.session.user:
		frappe.set_user(user)
	if user:
		frappe.only_for(constants.ALLOWED_ROLES)

	doc = frappe.get_doc(constants.BACKUP_DOCTYPE, backup_name)
	settings = utils.get_settings()

	started = now_datetime()
	context = frappe._dict(
		backup_name=doc.name,
		backup_type=doc.get("backup_type"),
		description=doc.get("description"),
		started_at=started,
	)

	staging_dir = None
	current = constants.STAGE_START
	try:
		_set(doc, status=constants.STATUS_RUNNING, started_at=started,
			current_stage=constants.STAGE_START, verification_status=constants.VERIFY_PENDING)

		backend = storage.get_backend(settings)
		root_name, archive_filename = _names(started, backend)
		staging_dir, staging_root = _make_staging(settings, root_name)

		# 1. Database (authoritative dump) ----------------------------------
		current = constants.STAGE_DATABASE
		_stage(doc, current)
		db_info = database.dump_database(staging_dir)
		context.database_size = db_info["db_size"]
		_set(doc, database_size=db_info["db_size"], database_backup_path=constants.DB_DUMP_NAME)

		# 2. Files ----------------------------------------------------------
		current = constants.STAGE_FILES
		_stage(doc, current)
		file_info = files.capture_files(staging_dir, db_info)
		context.files_size = file_info["files_size"]
		context.file_count = frappe.db.count("File")
		_set(doc, files_size=file_info["files_size"])

		# 3. Data export (Layer 2) -----------------------------------------
		current = constants.STAGE_DATA
		_stage(doc, current)
		data_info = data_export.export_business_data(staging_dir, settings)
		context.doctype_count = data_info["doctype_count"]
		context.data_row_count = data_info["row_count"]
		context.exported_doctypes = data_info["exported"]

		# 4. Metadata -------------------------------------------------------
		current = constants.STAGE_METADATA
		_stage(doc, current)
		meta_info = metadata.export_metadata(staging_dir)
		context.environment = meta_info["environment"]
		_set(doc,
			frappe_version=context.environment.get("frappe_version"),
			erpnext_version=context.environment.get("erpnext_version"))

		# 5. Manifest -------------------------------------------------------
		# archive_size + checksum are inherently unknown until the archive exists
		# (a file cannot checksum itself), so the in-archive manifest carries the
		# full picture EXCEPT those two — the ERP Backup record holds the
		# authoritative size + checksum once packaging completes.
		current = constants.STAGE_MANIFEST
		_stage(doc, current)
		context.backup_status = constants.STATUS_RUNNING
		manifest_mod.write_manifest(staging_dir, manifest_mod.build_manifest(context))

		# 6-7. Archive + checksum ------------------------------------------
		current = constants.STAGE_ARCHIVE
		_stage(doc, current)
		tmp_archive = os.path.join(staging_root, archive_filename)
		context.archive_size = archive_mod.package(staging_dir, tmp_archive, root_name)
		context.checksum = archive_mod.checksum(tmp_archive)
		final_location = backend.save(tmp_archive, archive_filename)
		_set(doc,
			archive_path=archive_filename,
			archive_size=context.archive_size,
			checksum=context.checksum,
			status=constants.STATUS_COMPLETED)

		# 8. Verify (never trust a mere file write) -------------------------
		current = constants.STAGE_VERIFY
		_stage(doc, current)
		result = verify.verify_archive(
			backend.path(archive_filename) or final_location, root_name, context.checksum
		)
		_set(doc,
			verification_status=constants.VERIFY_PASSED if result["ok"] else constants.VERIFY_FAILED,
			verification_details=_format_checks(result))
		if not result["ok"]:
			raise verify_failed(result["details"])

		# Verified — the only success path.
		_set(doc, status=constants.STATUS_VERIFIED, completed_at=now_datetime(),
			current_stage="Done")

		# 9. Retention ------------------------------------------------------
		current = constants.STAGE_RETENTION
		_stage(doc, current)
		_safe(lambda: retention.apply_retention(settings), "retention")

		# 10. Notify success (only after Verified) --------------------------
		current = constants.STAGE_NOTIFY
		_stage(doc, current)
		_safe(lambda: notifications.notify_success(doc, settings), "success notification")

		return {"name": doc.name, "status": doc.status, "checksum": context.checksum}

	except Exception as exc:
		frappe.db.rollback()
		_mark_failed(doc, current, exc)
		_safe(lambda: notifications.notify_failure(doc, current, frappe.get_traceback(), settings),
			"failure notification")
		frappe.log_error(frappe.get_traceback(), f"ERP Backup failed: {doc.name} @ {current}")
		return {"name": doc.name, "status": constants.STATUS_FAILED, "stage": current}
	finally:
		if staging_dir:
			_cleanup(os.path.dirname(staging_dir), root_name_dir=staging_dir)


# --- helpers ---------------------------------------------------------------

def _names(started, backend):
	base = f"ERP_BACKUP_{started.strftime('%Y-%m-%d')}"
	if backend.exists(base + ".tar.gz"):
		base = f"{base}_{started.strftime('%H%M%S')}"
	return base, base + ".tar.gz"


def _make_staging(settings, root_name):
	staging_root = os.path.join(utils.get_storage_root(settings), ".staging")
	staging_dir = os.path.join(staging_root, root_name)
	if os.path.isdir(staging_dir):
		shutil.rmtree(staging_dir, ignore_errors=True)
	os.makedirs(staging_dir, exist_ok=True)
	return staging_dir, staging_root


def _set(doc, **values):
	"""Persist field values on the ERP Backup record and commit (live progress)."""
	for field, value in values.items():
		doc.set(field, value)
	doc.save(ignore_permissions=True)
	frappe.db.commit()


def _stage(doc, stage_label):
	_set(doc, current_stage=stage_label)


def _mark_failed(doc, stage, exc):
	try:
		doc.reload()
	except Exception:
		pass
	doc.status = constants.STATUS_FAILED
	doc.verification_status = constants.VERIFY_FAILED
	doc.current_stage = stage
	doc.completed_at = now_datetime()
	doc.error_log = f"Stage: {stage}\n\n{frappe.get_traceback()}"
	try:
		doc.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"ERP Backup: could not mark {doc.name} failed")


def _format_checks(result):
	lines = [result.get("details", "")]
	for check in result.get("checks", []):
		mark = "PASS" if check["passed"] else "FAIL"
		info = f" — {check['info']}" if check.get("info") else ""
		lines.append(f"[{mark}] {check['name']}{info}")
	return "\n".join(lines)


def _safe(fn, label):
	"""Run a non-critical post-success step; log but never fail the backup on it."""
	try:
		fn()
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"ERP Backup: {label} failed")


def _cleanup(staging_root, root_name_dir):
	try:
		if root_name_dir and os.path.isdir(root_name_dir):
			shutil.rmtree(root_name_dir, ignore_errors=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "ERP Backup: staging cleanup failed")


def verify_failed(details):
	return frappe.ValidationError(f"Backup verification failed: {details}")
