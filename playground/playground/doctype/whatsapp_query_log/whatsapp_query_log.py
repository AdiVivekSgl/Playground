# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now


class WhatsAppQueryLog(Document):
	@staticmethod
	def clear_old_logs(days=90):
		# Called by Log Settings (registered via default_log_clearing_doctypes in hooks.py).
		table = frappe.qb.DocType("WhatsApp Query Log")
		frappe.db.delete(table, filters=(table.creation < (Now() - Interval(days=days))))
