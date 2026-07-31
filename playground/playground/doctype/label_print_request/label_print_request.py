# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Label Print Request
===================

A single row in the finished-kit label print queue and its audit trail.

Rows are NOT created by hand in normal operation - one is created automatically
when a Work Order enters "In Process" (see
playground.playground.label_printing.on_work_order_update). A lightweight print
agent running on the Windows PC next to the TSC printer polls the REST API for
Status = "Pending" rows, renders TSPL, prints, and writes back Status = "Printed"
(with `printed_on`) or "Failed" (with `error_log`).

The controller only guards the queue's invariants; all the printing lives in the
external agent, and all the queue-creation logic lives in `label_printing`.
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime


class LabelPrintRequest(Document):
	def validate(self):
		if cint(self.number_of_labels) <= 0:
			frappe.throw(_("Number of Labels must be greater than zero."))

		# When the agent marks a row Printed, stamp the time if it didn't; when a
		# row goes back to Pending (a reprint / manual retry), clear the old outcome
		# so the audit trail can't show a stale "Printed On" against a Pending row.
		if self.status == "Printed" and not self.printed_on:
			self.printed_on = now_datetime()
		elif self.status == "Pending":
			self.printed_on = None
			self.error_log = None
