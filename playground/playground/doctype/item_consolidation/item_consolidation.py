# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from playground.playground.item_consolidation import api, checks
from playground.playground.item_consolidation.api import block_open_documents


class ItemConsolidation(Document):
	def validate(self):
		errors = checks.validate_pair_rows(self.items)
		if errors:
			frappe.throw("<br>".join(errors), title=_("Invalid Item Pairs"))
		if self.docstatus == 0:
			self.status = "Draft"

	def before_submit(self):
		self._check_permissions()
		# Fail fast on anything the job would refuse; it re-checks each pair anyway
		# since stock and documents can change before the job runs.
		problems = []
		for row in self.items:
			ctx = checks.check_pair(
				row.old_item, row.keep_item, self.company, block_open_documents=block_open_documents(self)
			)
			problems.extend(_("Row {0} ({1}): {2}").format(row.idx, row.old_item, b) for b in ctx.blockers)
		if problems:
			frappe.throw("<br>".join(problems), title=_("Resolve these first (see Dry Run)"))
		for row in self.items:
			row.status = "Pending"

	def on_submit(self):
		api.enqueue(self)

	def before_cancel(self):
		frappe.throw(
			_(
				"An Item Consolidation cannot be cancelled. Cancel its Repack Stock Entries and "
				"re-enable the items individually if a pair must be undone."
			)
		)

	def _check_permissions(self):
		needed = [("Stock Entry", "submit"), ("Item", "write")]
		if cint(self.replace_in_boms):
			needed.append(("BOM", "write"))
		if cint(self.copy_masters):
			needed.append(("Item Price", "create"))
		missing = [f"{dt} ({ptype})" for dt, ptype in needed if not frappe.has_permission(dt, ptype)]
		if missing:
			frappe.throw(
				_("You need these permissions to run an Item Consolidation: {0}").format(", ".join(missing)),
				frappe.PermissionError,
			)
