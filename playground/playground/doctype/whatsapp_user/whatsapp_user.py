# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from playground.playground.whatsapp.phone import MAPPING_DOCTYPE, RESERVED_USERS, normalize_phone


class WhatsAppUser(Document):
	def validate(self):
		normalized = normalize_phone(self.phone)
		if not normalized:
			frappe.throw(_("Enter a valid WhatsApp number, e.g. +91 98123 45678."))
		self.phone = normalized

		if frappe.db.exists(MAPPING_DOCTYPE, {"phone": normalized, "name": ("!=", self.name)}):
			frappe.throw(_("WhatsApp number {0} is already mapped to another user.").format(normalized))

		# Administrator bypasses every permission check and Guest is "nobody" -
		# neither may be reachable from WhatsApp.
		if self.user in RESERVED_USERS:
			frappe.throw(_("{0} cannot be linked to a WhatsApp number.").format(self.user))
