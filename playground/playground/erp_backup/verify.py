# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Stage 8: archive verification (spec 10).

A backup is NEVER marked ``Verified`` just because a file was written (spec 7). We
re-open the finished archive and confirm it is a readable tar, that the expected
directories exist, that the database dump is present and non-zero, that the
manifest is present, and that the checksum matches. Each check is reported so the
result can be stored on the ERP Backup record and shown to the administrator.

The structured result is intentionally simple so a future, more thorough
restore-test (spec 11) can extend it: add checks to ``verify_archive`` (or a new
``restore_test`` module) and append to ``result["checks"]``.
"""

import os
import tarfile

from playground.playground.erp_backup import archive as archive_mod
from playground.playground.erp_backup import constants


def verify_archive(archive_path, root_name, expected_checksum=None):
	"""Verify an archive. Returns ``{"ok": bool, "checks": [...], "details": str}``.

	``checks`` is a list of ``{"name", "passed", "info"}``. ``ok`` is True only if
	every check passed.
	"""
	checks = []

	def record(name, passed, info=""):
		checks.append({"name": name, "passed": bool(passed), "info": info})
		return passed

	# 1. Archive exists and is non-empty.
	if not (archive_path and os.path.isfile(archive_path)):
		record("archive_exists", False, "archive file not found")
		return _result(checks)
	size = os.path.getsize(archive_path)
	if not record("archive_non_empty", size > 0, f"{size} bytes"):
		return _result(checks)

	# 2. Archive opens as a readable tar and its members can be listed. A corrupt
	#    or truncated archive raises here and is caught as a failed check.
	try:
		with tarfile.open(archive_path, "r:gz") as tar:
			names = tar.getnames()
			db_member = _find(tar, root_name, constants.DIR_DATABASE, constants.DB_DUMP_NAME)
	except (tarfile.TarError, OSError, EOFError) as exc:
		record("archive_readable", False, f"cannot open archive: {exc}")
		return _result(checks)
	record("archive_readable", True, f"{len(names)} members")

	# 3. Expected top-level directories present.
	for directory in (constants.DIR_DATABASE, constants.DIR_FILES, constants.DIR_DATA, constants.DIR_METADATA):
		present = any(n == f"{root_name}/{directory}" or n.startswith(f"{root_name}/{directory}/") for n in names)
		record(f"dir_{directory.lower()}_present", present, "" if present else "missing")

	# 4. Database dump present and non-zero.
	if db_member is None:
		record("database_dump_present", False, "database.sql.gz missing")
	else:
		record("database_dump_present", True)
		record("database_dump_non_empty", db_member.size > 0, f"{db_member.size} bytes")

	# 5. Manifest present.
	manifest_present = f"{root_name}/{constants.MANIFEST_NAME}" in names
	record("manifest_present", manifest_present, "" if manifest_present else "manifest missing")

	# 6. Checksum matches (when provided).
	if expected_checksum:
		actual = archive_mod.checksum(archive_path)
		record("checksum_matches", actual == expected_checksum,
			"match" if actual == expected_checksum else "checksum mismatch")

	return _result(checks)


def _find(tar, root_name, *parts):
	target = "/".join((root_name, *parts))
	try:
		return tar.getmember(target)
	except KeyError:
		return None


def _result(checks):
	ok = all(c["passed"] for c in checks) and bool(checks)
	failed = [c["name"] for c in checks if not c["passed"]]
	details = "All checks passed." if ok else "Failed checks: " + ", ".join(failed)
	return {"ok": ok, "checks": checks, "details": details}
