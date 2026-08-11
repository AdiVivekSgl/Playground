import frappe
from frappe.model.document import Document


class CRMOpportunity(Document):
	def validate(self):
		# Status mirrors the terminal stages; everything else is treated as Open.
		if self.stage in ("Won", "Lost"):
			self.status = self.stage
		else:
			self.status = "Open"
