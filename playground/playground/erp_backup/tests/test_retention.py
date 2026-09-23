# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Retention tests (spec 6, 19).

Keeps the newest N monthly backups; preserves Year-End and 'Keep Forever' backups
permanently; prunes the rest (archive + record).
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from playground.playground.erp_backup import constants, retention


def _row(name, backup_type="Monthly", keep_forever=0):
	return {
		"name": name, "backup_type": backup_type, "keep_forever": keep_forever,
		"archive_path": name + ".tar.gz", "started_at": name,
	}


class TestRetention(unittest.TestCase):
	def _run(self, rows, keep_n=2, keep_year_end=1):
		backend = MagicMock()
		delete_doc = MagicMock()
		settings = frappe._dict(retention_monthly_count=keep_n, keep_year_end_forever=keep_year_end)
		with patch.object(retention.frappe, "get_all", return_value=rows), \
			patch.object(retention.storage, "get_backend", return_value=backend), \
			patch.object(retention.frappe, "delete_doc", delete_doc), \
			patch.object(retention.frappe, "log_error", MagicMock()):
			result = retention.apply_retention(settings)
		return result, backend, delete_doc

	def test_prunes_oldest_beyond_window(self):
		# Newest first: keep 2 monthly, protect year-end + keep-forever, prune rest.
		rows = [
			_row("m1"), _row("m2"),
			_row("ye", backup_type=constants.TYPE_YEAR_END),
			_row("kf", keep_forever=1),
			_row("m5"), _row("m6"),
		]
		result, backend, delete_doc = self._run(rows, keep_n=2)
		self.assertEqual(set(result["deleted"]), {"m5", "m6"})
		deleted_names = {c.args[1] for c in delete_doc.call_args_list}
		self.assertEqual(deleted_names, {"m5", "m6"})

	def test_year_end_preserved(self):
		rows = [_row("m1"), _row("m2"), _row("m3"), _row("ye", backup_type=constants.TYPE_YEAR_END)]
		result, _, _ = self._run(rows, keep_n=1)
		self.assertNotIn("ye", result["deleted"])
		self.assertIn("m3", result["deleted"])  # 2nd/3rd monthly beyond window

	def test_keep_forever_preserved(self):
		rows = [_row("m1"), _row("kf", keep_forever=1), _row("m3")]
		result, _, _ = self._run(rows, keep_n=1)
		self.assertNotIn("kf", result["deleted"])

	def test_nothing_pruned_within_window(self):
		rows = [_row("m1"), _row("m2")]
		result, _, delete_doc = self._run(rows, keep_n=12)
		self.assertEqual(result["deleted"], [])
		delete_doc.assert_not_called()


if __name__ == "__main__":
	unittest.main()
