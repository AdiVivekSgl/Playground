import frappe
from frappe.model.document import Document


class GmailThread(Document):
	def validate(self):
		if self.crm_opportunity and self.status == "Unlinked":
			self.status = "Linked"
