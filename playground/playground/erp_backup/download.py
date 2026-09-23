# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Secure, streaming backup download (spec 8, 15, 16).

Backups are the entire ERP. They live in the site's PRIVATE area and are served
ONLY through this permission-gated endpoint — never via ``/files`` or any guessable
public URL. The resolved path is checked to lie inside the storage root so no
crafted key can escape it, and the file is streamed, never read whole into memory:

the file is streamed with ``wrap_file`` (a lazy, chunked iterator) so a multi-GB
archive never loads into memory. Returning a real ``Response`` from a whitelisted
method is supported — it is exactly how Frappe's own backup download works.
"""

import os

import frappe

from playground.playground.erp_backup import constants, utils


def check_download_permission():
	"""Raise ``frappe.PermissionError`` unless the user may download backups."""
	frappe.only_for(constants.ALLOWED_ROLES)


def stream_backup(name):
	"""Return a streaming download response for ERP Backup ``name``.

	Enforces permission, resolves the archive strictly within the storage root,
	and streams it. Raises if the user is unauthorised or the archive is missing.
	"""
	check_download_permission()

	doc = frappe.get_doc(constants.BACKUP_DOCTYPE, name)
	archive_key = doc.get("archive_path")
	if not archive_key:
		frappe.throw(
			frappe._("Backup {0} has no downloadable archive (status: {1}).").format(
				name, doc.get("status")
			)
		)

	settings = utils.get_settings()
	root = utils.get_storage_root(settings)
	path = os.path.realpath(os.path.join(root, os.path.basename(archive_key)))

	# Traversal guard: the file must resolve inside the storage root.
	if not utils.is_within(path, root):
		raise frappe.PermissionError("Backup path outside storage root")
	if not os.path.isfile(path):
		frappe.throw(frappe._("Backup archive file is missing on disk: {0}").format(name))

	return _wrap_file_response(path)


def _wrap_file_response(path):
	"""Chunked, streamed download response (never reads the whole file into RAM)."""
	from werkzeug.wrappers import Response
	from werkzeug.wsgi import wrap_file

	size = os.path.getsize(path)
	filename = os.path.basename(path)
	handle = open(path, "rb")  # closed by werkzeug when the stream is exhausted

	environ = getattr(getattr(frappe.local, "request", None), "environ", {}) or {}
	response = Response(wrap_file(environ, handle), direct_passthrough=True)
	response.headers["Content-Type"] = "application/gzip"
	response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
	response.headers["Content-Length"] = str(size)
	return response
