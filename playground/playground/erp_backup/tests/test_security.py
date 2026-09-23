# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Security tests (spec 8, 15, 19).

An unauthorized user can neither trigger a backup nor download one. Authorization
is enforced by ``frappe.only_for`` at the top of both entry points; here we make it
raise (as it would for a user lacking the role) and assert the operation aborts
BEFORE any side effect (no record inserted, no job enqueued, no file opened).
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from playground.playground.erp_backup import api, download


class TestTriggerAuthorization(unittest.TestCase):
	def test_unauthorized_cannot_trigger(self):
		enqueue = MagicMock()
		with patch.object(api.frappe, "only_for", side_effect=frappe.PermissionError), \
			patch.object(api.frappe, "new_doc") as new_doc, \
			patch.object(api.frappe, "enqueue", enqueue):
			with self.assertRaises(frappe.PermissionError):
				api.create_backup_now(backup_type="Manual")
		new_doc.assert_not_called()
		enqueue.assert_not_called()


class TestDownloadAuthorization(unittest.TestCase):
	def test_unauthorized_cannot_download(self):
		with patch.object(download.frappe, "only_for", side_effect=frappe.PermissionError), \
			patch.object(download.frappe, "get_doc") as get_doc:
			with self.assertRaises(frappe.PermissionError):
				download.stream_backup("ERPBKP-2026-00001")
		# Aborted before ever loading the backup record.
		get_doc.assert_not_called()


if __name__ == "__main__":
	unittest.main()
