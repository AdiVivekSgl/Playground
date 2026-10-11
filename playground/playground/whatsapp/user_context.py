# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Run a business lookup as the mapped ERPNext user.

The endpoint runs as Guest (allow_guest + shared secret). A handler that reads
ERPNext data switches to the sender's mapped user for the duration of the
lookup, so every permission check Frappe does (roles, User Permissions,
permission_query_conditions) applies to that user, and then switches back -
even when the lookup raises - so the rest of the request (logging, session
handling) is Guest again.
"""

from contextlib import contextmanager

import frappe

from playground.playground.whatsapp.phone import RESERVED_USERS


@contextmanager
def as_user(user):
	if not user or user in RESERVED_USERS:
		# get_user_from_phone never returns these; refuse rather than escalate if it ever did.
		raise frappe.PermissionError(f"WhatsApp lookups cannot run as {user!r}")
	original = frappe.session.user
	frappe.set_user(user)
	try:
		yield user
	finally:
		frappe.set_user(original)
