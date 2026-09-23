# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 6-7: package the staging tree into a compressed archive + checksum.

``tarfile`` adds members by path, streaming each file from disk through the gzip
compressor — the whole archive is never held in memory (spec 4, 16). The archive's
single top-level directory is ``ERP_BACKUP_YYYY-MM-DD`` so it unpacks cleanly
(spec 4). The checksum is a streamed SHA-256 read in fixed chunks.
"""

import hashlib
import os
import tarfile

from playground.playground.erp_backup import constants


def package(staging_dir, archive_path, root_name):
	"""Compress ``staging_dir`` into ``archive_path`` under top folder ``root_name``.

	Returns the archive size in bytes.
	"""
	os.makedirs(os.path.dirname(archive_path), exist_ok=True)
	with tarfile.open(archive_path, "w:gz") as tar:
		# arcname makes every member sit under ERP_BACKUP_YYYY-MM-DD/ inside the tar.
		tar.add(staging_dir, arcname=root_name)
	return os.path.getsize(archive_path)


def checksum(path):
	"""Streamed SHA-256 of a (potentially very large) file."""
	digest = hashlib.sha256()
	with open(path, "rb") as handle:
		for chunk in iter(lambda: handle.read(constants.CHUNK_SIZE), b""):
			digest.update(chunk)
	return digest.hexdigest()
