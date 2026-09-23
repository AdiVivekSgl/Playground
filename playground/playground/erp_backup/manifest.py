# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 5: the backup manifest (spec 3).

``BACKUP_MANIFEST.json`` makes an old archive understandable without access to the
ERP: what it is, when it was taken, from which versions, how big, and whether it
verified. It is written into the staging root before the archive is packaged; the
``archive_size`` and ``checksum`` are filled in by the pipeline after packaging and
the manifest inside the ERP Backup record is updated to match.
"""

import json
import os

import frappe

from playground.playground.erp_backup import constants, utils


def build_manifest(context):
	"""Assemble the manifest dict from the pipeline ``context`` (a frappe._dict)."""
	env = context.get("environment") or {}
	data = {
		"backup_date": context.get("started_at") or frappe.utils.now(),
		"backup_type": context.get("backup_type"),
		"description": context.get("description"),
		"site": env.get("site") or frappe.local.site,
		"frappe_version": env.get("frappe_version"),
		"erpnext_version": env.get("erpnext_version"),
		"installed_apps": env.get("installed_apps") or [],
		"app_versions": env.get("app_versions") or {},
		"database_size": context.get("database_size") or 0,
		"files_size": context.get("files_size") or 0,
		"archive_size": context.get("archive_size") or 0,
		"doctype_count": context.get("doctype_count") or 0,
		"data_row_count": context.get("data_row_count") or 0,
		"exported_doctypes": context.get("exported_doctypes") or [],
		"file_count": context.get("file_count") or 0,
		"backup_status": context.get("backup_status") or constants.STATUS_RUNNING,
		"checksum": context.get("checksum"),
		"erp_backup_record": context.get("backup_name"),
		"generated_by": frappe.session.user,
	}
	return utils.scrub_secrets(data)


def write_manifest(staging_dir, manifest):
	path = os.path.join(staging_dir, constants.MANIFEST_NAME)
	with open(path, "w", encoding="utf-8") as handle:
		json.dump(manifest, handle, indent=2, default=str, ensure_ascii=False)
	return path
