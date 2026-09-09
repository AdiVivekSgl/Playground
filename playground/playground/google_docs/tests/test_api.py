# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Orchestration tests: duplicate protection, permission enforcement and the
'never look successful on failure' guarantee (spec 14). Google + DB touch points
are mocked so these run without a live site or credentials.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from playground.playground.google_docs import api, permissions
from playground.playground.google_docs.constants import STATUS_ERROR
from playground.playground.google_docs.exceptions import GoogleApiError, GoogleDocsError


class TestDuplicateProtection(unittest.TestCase):
	def test_create_refuses_when_doc_exists(self):
		with patch.object(permissions, "check_erp_permission"), patch.object(
			api, "_find_template", return_value=frappe._dict(name="T")
		), patch.object(api, "_read_metadata", return_value={"id": "existing-doc-id"}):
			with self.assertRaises(GoogleDocsError):
				api.create_google_document("Purchase Order", "PO-1")

	def test_force_new_bypasses_duplicate_guard(self):
		with patch.object(permissions, "check_erp_permission"), patch.object(
			api, "_find_template", return_value=frappe._dict(name="T")
		), patch.object(api, "_read_metadata", return_value={"id": "existing"}), patch.object(
			api, "_dispatch", return_value={"queued": False}
		) as dispatch:
			api.create_google_document("Purchase Order", "PO-1", force_new=1)
			dispatch.assert_called_once()

	def test_update_requires_existing_doc(self):
		with patch.object(permissions, "check_erp_permission"), patch.object(
			api, "_find_template", return_value=frappe._dict(name="T")
		), patch.object(api, "_read_metadata", return_value={"id": None}):
			with self.assertRaises(GoogleDocsError):
				api.update_google_document("Purchase Order", "PO-1")


class TestPermissions(unittest.TestCase):
	def test_permission_error_propagates(self):
		with patch.object(
			permissions, "check_erp_permission", side_effect=frappe.PermissionError
		):
			with self.assertRaises(frappe.PermissionError):
				api.create_google_document("Purchase Order", "PO-1")


class TestFailureIsVisible(unittest.TestCase):
	def test_error_status_written_and_raised_on_google_failure(self):
		writes = []

		def _record_meta(doctype, name, **kwargs):
			writes.append(kwargs)

		with patch.object(
			permissions, "get_document", return_value=frappe._dict(doctype="Purchase Order", name="PO-1")
		), patch.object(api, "_find_template", return_value=frappe._dict(name="T")), patch.object(
			api, "get_settings", return_value=frappe._dict()
		), patch.object(api, "_create_copy", side_effect=GoogleApiError("boom")), patch.object(
			api, "_write_metadata", side_effect=_record_meta
		), patch.object(api, "_read_metadata", return_value={"id": None}), patch.object(
			api, "_log"
		), patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"):
			with self.assertRaises(Exception):
				api._generate("Purchase Order", "PO-1", is_update=False)

		# The ERP document must be flagged as errored, not left looking successful.
		self.assertTrue(any(w.get("status") == STATUS_ERROR for w in writes))
