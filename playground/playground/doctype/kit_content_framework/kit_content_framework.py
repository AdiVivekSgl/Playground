import frappe
from frappe import _
from frappe.model.document import Document


class KitContentFramework(Document):
	def validate(self):
		self.validate_indent_levels()

	def validate_indent_levels(self):
		"""The tree shape is implicit: a row's parent is the nearest preceding
		row with indent_level - 1. That only stays unambiguous if no row jumps
		more than one level deeper than the row before it, so we enforce that
		here rather than discovering a broken tree later at BOM-generation time.
		"""
		previous_level = 0
		for row in self.items:
			if not row.indent_level or row.indent_level < 1:
				frappe.throw(
					_("Row #{0} ({1}): Indent Level must be 1 or greater.").format(
						row.idx, row.node_name
					)
				)
			if row.indent_level > previous_level + 1:
				frappe.throw(
					_(
						"Row #{0} ({1}): Indent Level {2} skips a level — it can be at "
						"most {3} here (one more than the row above it)."
					).format(row.idx, row.node_name, row.indent_level, previous_level + 1)
				)
			previous_level = row.indent_level
