import frappe
from frappe.model.document import Document


class CRMActivity(Document):
	def validate(self):
		if not self.user:
			self.user = frappe.session.user
		if self.activity_type == "Follow-up" and not self.follow_up_status:
			self.follow_up_status = "Open"
