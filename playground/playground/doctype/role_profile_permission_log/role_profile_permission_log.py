# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Role Profile Permission Log
===========================

An immutable audit record of a single Role Profile permission-workbook apply.

Rows are created only by
playground.playground.role_profile_permissions.apply_import - never by hand -
and are meant to be read-only forever. The DocType grants no `write` permission to
anyone (only System Managers may create / delete), and this controller is a second
line of defence: it rejects any edit to an already-saved log, even one attempted
with ignore_permissions. It closes the "no audit trail of who applied which
workbook / what changed" gap the workbook originally shipped with.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class RoleProfilePermissionLog(Document):
	def before_save(self):
		# get_doc_before_save() is None on the initial insert and the previously
		# stored version on any later save - so this fires only on edits.
		if self.get_doc_before_save() is not None:
			frappe.throw(_("Role Profile Permission Log entries are an audit trail and cannot be edited."))
