# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Manifest + secret-scrubbing tests (spec 2, 3, 15, 19).

The manifest carries every field the spec requires, and NO credential ever reaches
the human-readable output — :func:`utils.scrub_secrets` drops password/key/token
style keys anywhere in the structure.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from playground.playground.erp_backup import constants, manifest as manifest_mod, utils


class TestManifest(unittest.TestCase):
	def _context(self):
		return frappe._dict(
			started_at="2026-09-15T02:00:00",
			backup_type=constants.TYPE_MONTHLY,
			description="desc",
			environment={
				"site": "site1", "frappe_version": "15.0.0", "erpnext_version": "15.1.0",
				"installed_apps": ["frappe", "erpnext", "playground"], "app_versions": {},
			},
			database_size=100, files_size=200, archive_size=300,
			doctype_count=5, data_row_count=50, exported_doctypes=["Customer"],
			file_count=42, checksum="abc123", backup_name="ERPBKP-1", backup_status="Verified",
		)

	def test_manifest_has_required_fields(self):
		with patch.object(manifest_mod, "frappe", MagicMock()):
			data = manifest_mod.build_manifest(self._context())
		for key in (
			"backup_date", "site", "frappe_version", "erpnext_version", "installed_apps",
			"database_size", "files_size", "archive_size", "doctype_count", "file_count",
			"backup_status", "checksum",
		):
			self.assertIn(key, data, f"manifest missing {key}")
		self.assertEqual(data["site"], "site1")
		self.assertEqual(data["frappe_version"], "15.0.0")
		self.assertEqual(data["database_size"], 100)
		self.assertEqual(data["file_count"], 42)
		self.assertEqual(data["checksum"], "abc123")
		self.assertEqual(data["backup_status"], "Verified")

	def test_manifest_contains_no_secret_keys(self):
		with patch.object(manifest_mod, "frappe", MagicMock()):
			data = manifest_mod.build_manifest(self._context())
		for key in data:
			self.assertFalse(
				any(hint in key.lower() for hint in constants.SECRET_KEY_HINTS),
				f"secret-looking key leaked: {key}",
			)


class TestScrubSecrets(unittest.TestCase):
	def test_drops_secret_keys_recursively(self):
		payload = {
			"password": "x", "api_key": "y", "name": "ok",
			"nested": {"secret": "z", "keep": 1, "auth_token": "t"},
			"rows": [{"token": "t", "value": 2}],
		}
		clean = utils.scrub_secrets(payload)
		self.assertEqual(clean, {"name": "ok", "nested": {"keep": 1}, "rows": [{"value": 2}]})


if __name__ == "__main__":
	unittest.main()
