import frappe
from frappe.model.document import Document


class CRMContact(Document):
	def validate(self):
		if self.email:
			self.email = self.email.strip().lower()
			self.email_domain = self.email.split("@")[-1] if "@" in self.email else None
		if (self.erpnext_customer or self.erpnext_contact or self.erpnext_lead) and self.status == "Unmapped":
			self.status = "Mapped"
