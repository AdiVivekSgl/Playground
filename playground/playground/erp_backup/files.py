# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 2: capture the site's public + private files.

Frappe's ``new_backup`` already tarred ``public/files`` and ``private/files`` for
us (see :mod:`database`). Those tars preserve exactly the directory structure
``bench restore --with-public-files/--with-private-files`` expects (spec 1B), so we
stage them as-is under ``FILES/`` rather than extracting and re-taring gigabytes of
attachments. Both are moved (not copied) to avoid a second on-disk copy.
"""

import os
import shutil

import frappe

from playground.playground.erp_backup import constants
from playground.playground.erp_backup.database import BackupStageError


def capture_files(staging_dir, db_info):
	"""Stage the public/private file tars produced by the database stage.

	``db_info`` is the dict returned by :func:`database.dump_database`. Returns
	``{"files_size": int, "public": <name|None>, "private": <name|None>}``.
	"""
	files_dir = os.path.join(staging_dir, constants.DIR_FILES)
	os.makedirs(files_dir, exist_ok=True)

	total = 0
	staged = {"public": None, "private": None}

	for key, src in (("public", db_info.get("files_tar")), ("private", db_info.get("private_tar"))):
		if not src or not os.path.isfile(src):
			# A site with no public (or no private) files simply has no tar — that
			# is normal, not a failure. Restoration just skips that flag.
			continue
		dest = os.path.join(files_dir, os.path.basename(src))
		shutil.move(src, dest)
		size = os.path.getsize(dest)
		total += size
		staged[key] = os.path.basename(dest)

	if staged["public"] is None and staged["private"] is None:
		# Extremely unusual: not even an empty tar. Treat as a file-stage failure
		# so the backup is not silently missing its attachments.
		raise BackupStageError(frappe._("No file archives were produced by the backup."))

	return {"files_size": total, **staged}
