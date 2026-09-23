# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Manual backup tests (spec 14, 19).

'Create Backup Now' must enqueue a background job and return immediately — the ERP
request never waits for the backup. We assert a Queued record is created and the
pipeline is enqueued (not run inline).
"""

import unittest
from unittest.mock import MagicMock, patch

from playground.playground.erp_backup import api, constants


class FakeNewDoc:
	def __init__(self):
		self.name = "ERPBKP-2026-00007"
		self.backup_type = None
		self.description = None
		self.keep_forever = None
		self.status = None

	def insert(self, *a, **k):
		return self


class TestManualBackup(unittest.TestCase):
	def test_create_backup_now_enqueues_and_returns(self):
		enqueue = MagicMock()
		doc = FakeNewDoc()
		with patch.object(api.frappe, "only_for"), \
			patch.object(api.frappe, "new_doc", return_value=doc), \
			patch.object(api.frappe, "db", MagicMock()), \
			patch.object(api.frappe, "enqueue", enqueue):
			result = api.create_backup_now(backup_type="Manual", description="nightly", keep_forever=1)

		# Returned immediately with the queued record.
		self.assertEqual(result["name"], "ERPBKP-2026-00007")
		self.assertTrue(result["queued"])
		self.assertEqual(doc.status, constants.STATUS_QUEUED)
		self.assertEqual(doc.keep_forever, 1)

		# The heavy work was enqueued, not executed inline.
		enqueue.assert_called_once()
		args, kwargs = enqueue.call_args
		self.assertEqual(args[0], "playground.playground.erp_backup.pipeline.run_backup")
		self.assertEqual(kwargs["backup_name"], "ERPBKP-2026-00007")
		self.assertEqual(kwargs["queue"], "long")

	def test_default_backup_type_is_manual(self):
		doc = FakeNewDoc()
		with patch.object(api.frappe, "only_for"), \
			patch.object(api.frappe, "new_doc", return_value=doc), \
			patch.object(api.frappe, "db", MagicMock()), \
			patch.object(api.frappe, "enqueue", MagicMock()):
			api.create_backup_now()
		self.assertEqual(doc.backup_type, constants.TYPE_MANUAL)


if __name__ == "__main__":
	unittest.main()
