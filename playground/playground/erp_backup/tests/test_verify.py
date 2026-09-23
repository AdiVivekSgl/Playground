# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Verification + archive tests (spec 10, 19).

These exercise real tar packaging and verification against real (tiny) files — no
frappe or DB is needed, because :mod:`verify` and :mod:`archive` depend only on the
standard library. Covers: a good archive verifies; a corrupt archive is detected;
a missing database dump is detected; a missing manifest is detected.
"""

import os
import shutil
import tempfile
import unittest

from playground.playground.erp_backup import archive, constants, verify


class ArchiveFixture:
	"""Build a staging tree + archive on disk; clean up on exit."""

	def __init__(self, with_db=True, with_manifest=True, db_bytes=b"-- sql dump\n"):
		self.tmp = tempfile.mkdtemp(prefix="erpbkp_test_")
		self.root_name = "ERP_BACKUP_2026-09-30"
		staging = os.path.join(self.tmp, self.root_name)
		for d in (constants.DIR_DATABASE, constants.DIR_FILES, constants.DIR_DATA, constants.DIR_METADATA):
			os.makedirs(os.path.join(staging, d), exist_ok=True)
		if with_db:
			with open(os.path.join(staging, constants.DIR_DATABASE, constants.DB_DUMP_NAME), "wb") as fh:
				fh.write(db_bytes)
		with open(os.path.join(staging, constants.DIR_DATA, "Customer.csv"), "w") as fh:
			fh.write("name\nCUST-1\n")
		if with_manifest:
			with open(os.path.join(staging, constants.MANIFEST_NAME), "w") as fh:
				fh.write('{"backup_status": "verified"}')
		self.staging = staging
		self.archive_path = os.path.join(self.tmp, self.root_name + ".tar.gz")
		archive.package(staging, self.archive_path, self.root_name)
		self.checksum = archive.checksum(self.archive_path)

	def corrupt(self):
		# Truncate the gzip stream so tarfile can no longer read it.
		with open(self.archive_path, "r+b") as fh:
			fh.truncate(20)

	def cleanup(self):
		shutil.rmtree(self.tmp, ignore_errors=True)


class TestVerify(unittest.TestCase):
	def _fixture(self, **kw):
		fx = ArchiveFixture(**kw)
		self.addCleanup(fx.cleanup)
		return fx

	def test_good_archive_verifies(self):
		fx = self._fixture()
		result = verify.verify_archive(fx.archive_path, fx.root_name, fx.checksum)
		self.assertTrue(result["ok"], result["details"])

	def test_checksum_mismatch_detected(self):
		fx = self._fixture()
		result = verify.verify_archive(fx.archive_path, fx.root_name, "deadbeef")
		self.assertFalse(result["ok"])
		self.assertIn("checksum_matches", [c["name"] for c in result["checks"] if not c["passed"]])

	def test_corrupt_archive_detected(self):
		fx = self._fixture()
		fx.corrupt()
		result = verify.verify_archive(fx.archive_path, fx.root_name, fx.checksum)
		self.assertFalse(result["ok"])

	def test_missing_database_detected(self):
		fx = self._fixture(with_db=False)
		result = verify.verify_archive(fx.archive_path, fx.root_name, fx.checksum)
		self.assertFalse(result["ok"])
		failed = [c["name"] for c in result["checks"] if not c["passed"]]
		self.assertIn("database_dump_present", failed)

	def test_empty_database_detected(self):
		fx = self._fixture(db_bytes=b"")
		result = verify.verify_archive(fx.archive_path, fx.root_name, fx.checksum)
		self.assertFalse(result["ok"])
		failed = [c["name"] for c in result["checks"] if not c["passed"]]
		self.assertIn("database_dump_non_empty", failed)

	def test_missing_manifest_detected(self):
		fx = self._fixture(with_manifest=False)
		result = verify.verify_archive(fx.archive_path, fx.root_name, fx.checksum)
		self.assertFalse(result["ok"])
		failed = [c["name"] for c in result["checks"] if not c["passed"]]
		self.assertIn("manifest_present", failed)

	def test_missing_file_detected(self):
		result = verify.verify_archive("/no/such/archive.tar.gz", "ERP_BACKUP_X", None)
		self.assertFalse(result["ok"])


if __name__ == "__main__":
	unittest.main()
