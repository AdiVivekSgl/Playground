import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import formatdate, now_datetime


class WeeklyPlanningSnapshot(Document):
	def validate(self):
		# Not doctype-level unique on snapshot_date: cancel+amend needs to be
		# able to insert a new doc at the same date once the old one is
		# cancelled. Only a SUBMITTED snapshot on the same date is blocked.
		duplicate = frappe.db.exists(
			"Weekly Planning Snapshot",
			{
				"snapshot_date": self.snapshot_date,
				"docstatus": 1,
				"name": ["!=", self.name],
			},
		)
		if duplicate:
			frappe.throw(
				_("A submitted Weekly Planning Snapshot ({0}) already exists for {1}.").format(
					duplicate, formatdate(self.snapshot_date)
				)
			)

	def on_submit(self):
		self.db_set("approved_by", frappe.session.user)
		self.db_set("approved_on", now_datetime())
