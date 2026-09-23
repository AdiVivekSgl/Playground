# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Storage abstraction for backup archives.

The pipeline never touches the filesystem directly for the *final* archive — it
goes through a ``StorageBackend`` so a future release can add S3 / GCS / Azure
without changing the backup logic (spec 9). V1 ships ``LocalStorage`` only;
``get_backend`` is the single factory the rest of the module calls.

Every backend deals in a *key* (the archive's file name, e.g.
``ERP_BACKUP_2026-09-30.tar.gz``) and streams to/from disk in chunks — nothing
loads a whole archive into memory (spec 16).
"""

import os
import shutil

import frappe

from playground.playground.erp_backup import constants, utils


class StorageBackend:
	"""Interface every storage backend implements.

	A key is an opaque archive identifier (its file name). ``location`` returns a
	backend-specific locator string stored on the ERP Backup record; for local
	storage that is the absolute path.
	"""

	def save(self, source_path, key):
		"""Persist the file at ``source_path`` under ``key``; return its location."""
		raise NotImplementedError

	def open_stream(self, key):
		"""Return an open binary file object for ``key`` (caller closes it)."""
		raise NotImplementedError

	def path(self, key):
		"""Absolute/local path for ``key`` where the concept applies (else None)."""
		raise NotImplementedError

	def exists(self, key):
		raise NotImplementedError

	def delete(self, key):
		raise NotImplementedError

	def list_keys(self):
		raise NotImplementedError


class LocalStorage(StorageBackend):
	"""Local filesystem backend rooted at the site's private backups directory."""

	def __init__(self, root):
		self.root = root
		os.makedirs(self.root, exist_ok=True)

	def _resolve(self, key):
		# key is a bare file name; never allow it to escape the root.
		candidate = os.path.join(self.root, os.path.basename(key))
		if not utils.is_within(candidate, self.root):
			raise frappe.PermissionError("Invalid backup key")
		return candidate

	def save(self, source_path, key):
		dest = self._resolve(key)
		if os.path.realpath(source_path) != os.path.realpath(dest):
			shutil.move(source_path, dest)
		return dest

	def open_stream(self, key):
		return open(self._resolve(key), "rb")

	def path(self, key):
		return self._resolve(key)

	def exists(self, key):
		return os.path.isfile(self._resolve(key))

	def delete(self, key):
		target = self._resolve(key)
		if os.path.isfile(target):
			os.remove(target)
			return True
		return False

	def list_keys(self):
		if not os.path.isdir(self.root):
			return []
		return sorted(
			name for name in os.listdir(self.root)
			if os.path.isfile(os.path.join(self.root, name))
		)


def get_backend(settings=None):
	"""Return the configured storage backend. V1 supports 'Local' only.

	Cloud backends (S3/GCS/Azure) are an intentional extension point: add a
	subclass and a branch here — the pipeline and download endpoint need no
	changes because they only use the ``StorageBackend`` interface.
	"""
	settings = settings or utils.get_settings()
	backend = (settings.get("storage_backend") or "Local").strip()
	if backend == "Local":
		return LocalStorage(utils.get_storage_root(settings))
	raise frappe.ValidationError(
		frappe._("Unsupported storage backend: {0}").format(backend)
	)
