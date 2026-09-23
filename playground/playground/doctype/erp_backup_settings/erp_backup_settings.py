# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ERPBackupSettings(Document):
	def validate(self):
		if self.day_of_month is not None and not (1 <= int(self.day_of_month) <= 28):
			frappe.throw(frappe._("Day of Month should be between 1 and 28."))
		if self.hour is not None and not (0 <= int(self.hour) <= 23):
			frappe.throw(frappe._("Hour should be between 0 and 23."))
		if self.retention_monthly_count is not None and int(self.retention_monthly_count) < 1:
			frappe.throw(frappe._("Keep N Monthly Backups should be at least 1."))
