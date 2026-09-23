# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Pipeline orchestration tests (spec 7, 13, 19).

Every stage is mocked so the test exercises CONTROL FLOW, not real backups:
  * the happy path runs all stages in order and ends 'Verified', with the success
    (not failure) notification sent;
  * a failure in ANY stage marks the backup 'Failed', records the stage, sends the
    failure notification, and NEVER sends success — proving 'the archive being
    created is not enough' (spec 7).
"""

import contextlib
import unittest
from unittest.mock import MagicMock, patch

import frappe

from playground.playground.erp_backup import constants, pipeline


class FakeDoc:
	def __init__(self, name="ERPBKP-2026-00001"):
		self._data = {"name": name, "backup_type": "Manual", "description": None,
			"status": constants.STATUS_QUEUED, "verification_status": constants.VERIFY_PENDING}

	def get(self, key, default=None):
		return self._data.get(key, default)

	def set(self, key, value):
		self._data[key] = value

	def save(self, ignore_permissions=False):
		pass

	def reload(self):
		pass

	def __getattr__(self, key):
		try:
			return self.__dict__["_data"][key]
		except KeyError:
			raise AttributeError(key)

	def __setattr__(self, key, value):
		if key == "_data":
			super().__setattr__(key, value)
		else:
			self._data[key] = value


class FakeBackend:
	def exists(self, key):
		return False

	def save(self, src, key):
		return "/final/" + key

	def path(self, key):
		return "/final/" + key

	def delete(self, key):
		return True


STAGE_RETURNS = {
	"dump_database": {"db_size": 100, "db_path": "x", "files_tar": "a", "private_tar": "b"},
	"capture_files": {"files_size": 200, "public": "a", "private": "b"},
	"export_business_data": {"doctype_count": 3, "row_count": 10, "exported": ["Customer"], "skipped": {}, "dir": "d"},
	"export_metadata": {"environment": {"frappe_version": "15.0.0", "erpnext_version": "15.0.0", "site": "s"}, "dir": "m"},
}


class PipelineHarness:
	"""Patches every collaborator of the pipeline; lets one stage fail on demand."""

	def __init__(self, failing_stage=None, verify_ok=True):
		self.failing_stage = failing_stage
		self.verify_ok = verify_ok
		self.doc = FakeDoc()
		self.notify_success = MagicMock()
		self.notify_failure = MagicMock()
		self.stack = contextlib.ExitStack()

	def _stage(self, name, ret):
		def fn(*a, **k):
			if self.failing_stage == name:
				raise RuntimeError(f"boom in {name}")
			return ret
		return fn

	def __enter__(self):
		s = self.stack
		s.enter_context(patch.object(pipeline.frappe, "get_doc", return_value=self.doc))
		s.enter_context(patch.object(pipeline.frappe, "db", MagicMock()))
		s.enter_context(patch.object(pipeline.frappe, "log_error", MagicMock()))
		s.enter_context(patch.object(pipeline.frappe, "get_traceback", return_value="TB"))
		s.enter_context(patch.object(pipeline.utils, "get_settings", return_value=frappe._dict()))
		s.enter_context(patch.object(pipeline.storage, "get_backend", return_value=FakeBackend()))
		s.enter_context(patch.object(pipeline, "_make_staging", return_value=("/tmp/stg", "/tmp")))
		s.enter_context(patch.object(pipeline, "_cleanup", MagicMock()))

		s.enter_context(patch.object(pipeline.database, "dump_database", side_effect=self._stage("dump_database", STAGE_RETURNS["dump_database"])))
		s.enter_context(patch.object(pipeline.files, "capture_files", side_effect=self._stage("capture_files", STAGE_RETURNS["capture_files"])))
		s.enter_context(patch.object(pipeline.data_export, "export_business_data", side_effect=self._stage("export_business_data", STAGE_RETURNS["export_business_data"])))
		s.enter_context(patch.object(pipeline.metadata, "export_metadata", side_effect=self._stage("export_metadata", STAGE_RETURNS["export_metadata"])))
		s.enter_context(patch.object(pipeline.manifest_mod, "build_manifest", return_value={}))
		s.enter_context(patch.object(pipeline.manifest_mod, "write_manifest", return_value="/tmp/stg/BACKUP_MANIFEST.json"))
		s.enter_context(patch.object(pipeline.archive_mod, "package", side_effect=self._stage("package", 12345)))
		s.enter_context(patch.object(pipeline.archive_mod, "checksum", return_value="abc123"))
		s.enter_context(patch.object(pipeline.verify, "verify_archive", side_effect=self._stage("verify", {"ok": self.verify_ok, "checks": [], "details": "d"})))
		s.enter_context(patch.object(pipeline.retention, "apply_retention", return_value={"deleted": [], "kept": 1}))
		s.enter_context(patch.object(pipeline.notifications, "notify_success", self.notify_success))
		s.enter_context(patch.object(pipeline.notifications, "notify_failure", self.notify_failure))
		return self

	def __exit__(self, *exc):
		self.stack.close()
		return False


class TestHappyPath(unittest.TestCase):
	def test_full_run_ends_verified(self):
		with PipelineHarness() as h:
			result = pipeline.run_backup("ERPBKP-2026-00001")
		self.assertEqual(h.doc.status, constants.STATUS_VERIFIED)
		self.assertEqual(h.doc.verification_status, constants.VERIFY_PASSED)
		self.assertEqual(result["status"], constants.STATUS_VERIFIED)

	def test_all_creation_stages_invoked(self):
		# Covers spec 19 "backup creation": db, files, csv, metadata, manifest, archive.
		with PipelineHarness() as h:
			pipeline.run_backup("ERPBKP-2026-00001")
			pipeline.database.dump_database.assert_called_once()
			pipeline.files.capture_files.assert_called_once()
			pipeline.data_export.export_business_data.assert_called_once()
			pipeline.metadata.export_metadata.assert_called_once()
			pipeline.manifest_mod.write_manifest.assert_called()
			pipeline.archive_mod.package.assert_called_once()
			pipeline.verify.verify_archive.assert_called_once()

	def test_success_notification_only(self):
		with PipelineHarness() as h:
			pipeline.run_backup("ERPBKP-2026-00001")
		h.notify_success.assert_called_once()
		h.notify_failure.assert_not_called()


class TestFailureHandling(unittest.TestCase):
	def _assert_failed(self, failing_stage=None, verify_ok=True):
		with PipelineHarness(failing_stage=failing_stage, verify_ok=verify_ok) as h:
			result = pipeline.run_backup("ERPBKP-2026-00001")
		self.assertEqual(h.doc.status, constants.STATUS_FAILED)
		self.assertEqual(h.doc.verification_status, constants.VERIFY_FAILED)
		self.assertEqual(result["status"], constants.STATUS_FAILED)
		h.notify_failure.assert_called_once()
		h.notify_success.assert_not_called()

	def test_database_failure(self):
		self._assert_failed(failing_stage="dump_database")

	def test_file_failure(self):
		self._assert_failed(failing_stage="capture_files")

	def test_archive_failure(self):
		self._assert_failed(failing_stage="package")

	def test_verification_failure(self):
		# Archive built fine, but verification reports not-ok -> must still Fail.
		self._assert_failed(verify_ok=False)


if __name__ == "__main__":
	unittest.main()
