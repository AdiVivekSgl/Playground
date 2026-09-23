# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Small shared helpers: settings access, storage-root resolution, formatting."""

import os

import frappe

from playground.playground.erp_backup import constants


def get_settings():
	"""Return the ``ERP Backup Settings`` singleton (cached)."""
	return frappe.get_cached_doc(constants.SETTINGS_DOCTYPE)


def get_storage_root(settings=None):
	"""Absolute directory where backup archives live.

	Defaults to the site's PRIVATE backups area — deliberately off any public
	web route (spec 8, 9, 15). Configurable via ``local_storage_path`` in
	ERP Backup Settings. The directory is created on demand.
	"""
	settings = settings or get_settings()
	path = (settings.get("local_storage_path") or "").strip()
	if not path:
		path = default_storage_root()
	os.makedirs(path, exist_ok=True)
	return path


def default_storage_root():
	return frappe.get_site_path("private", "backups", "erp_monthly")


def is_within(path, root):
	"""True if ``path`` resolves to a location inside ``root`` (traversal guard)."""
	root_real = os.path.realpath(root)
	path_real = os.path.realpath(path)
	return path_real == root_real or path_real.startswith(root_real + os.sep)


def human_size(num_bytes):
	"""Human-readable byte size, e.g. 5368709120 -> '5.0 GB'."""
	try:
		num = float(num_bytes or 0)
	except (TypeError, ValueError):
		return "0 B"
	for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
		if abs(num) < 1024.0:
			return f"{num:3.1f} {unit}".strip()
		num /= 1024.0
	return f"{num:.1f} EB"


def scrub_secrets(value):
	"""Recursively drop keys that look like credentials from dicts/lists.

	A defensive net over the metadata/manifest exports so no password, key or
	token is ever written to the human-readable output (spec 2, 15).
	"""
	if isinstance(value, dict):
		clean = {}
		for key, val in value.items():
			if any(hint in str(key).lower() for hint in constants.SECRET_KEY_HINTS):
				continue
			clean[key] = scrub_secrets(val)
		return clean
	if isinstance(value, (list, tuple)):
		return [scrub_secrets(item) for item in value]
	return value
