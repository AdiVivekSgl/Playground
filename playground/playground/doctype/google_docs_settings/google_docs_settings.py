# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class GoogleDocsSettings(Document):
	pass


@frappe.whitelist()
def test_connection():
	"""Called by the 'Test Connection' button — surfaces auth problems early."""
	frappe.only_for("System Manager")
	from playground.playground.google_docs.auth import test_connection as _test

	_test()
	return {"ok": True, "message": frappe._("Google connection succeeded.")}
