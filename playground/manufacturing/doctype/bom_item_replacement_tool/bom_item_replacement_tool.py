# Copyright (c) 2026, Playground and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class BOMItemReplacementTool(Document):
	def validate(self):
		if self.old_item and self.new_item and self.old_item == self.new_item:
			frappe.throw(_("Old Item and New Item cannot be the same."))

	@frappe.whitelist()
	def generate_preview(self):
		self.validate_required_items()
		self.set("replacement_details", [])

		conditions = [
			"bi.item_code = %(old_item)s",
			"b.docstatus = 1",
		]
		if self.only_active_boms:
			conditions.append("b.is_active = 1")
		if self.company:
			conditions.append("b.company = %(company)s")

		rows = frappe.db.sql(
			f"""
			SELECT DISTINCT
				b.name,
				b.item,
				bi.qty
			FROM `tabBOM` b
			INNER JOIN `tabBOM Item` bi
				ON bi.parent = b.name
			WHERE {' AND '.join(conditions)}
			ORDER BY b.item
			""",
			{"old_item": self.old_item, "company": self.company},
			as_dict=True,
		)

		for row in rows:
			self.append(
				"replacement_details",
				{
					"bom": row.name,
					"item": row.item,
					"old_item": self.old_item,
					"new_item": self.new_item,
					"qty": row.qty,
					"status": "Pending",
				},
			)

		self.status = "Preview Generated"
		self.save()
		return len(rows)

	@frappe.whitelist()
	def enqueue_replacement(self):
		self.validate_execute()
		frappe.enqueue(
			method=execute_replacement,
			queue="long",
			timeout=5000,
			docname=self.name,
		)
		frappe.msgprint(_("Replacement has been queued in background."))

	def validate_required_items(self):
		if not self.old_item:
			frappe.throw(_("Old Item is required."))
		if not self.new_item:
			frappe.throw(_("New Item is required."))
		if self.old_item == self.new_item:
			frappe.throw(_("Old Item and New Item cannot be the same."))

	def validate_execute(self):
		self.validate_required_items()
		if self.status != "Preview Generated" or not self.replacement_details:
			frappe.throw(_("Generate preview before executing replacement."))

		new_item = frappe.get_cached_doc("Item", self.new_item)
		if new_item.disabled:
			frappe.throw(_("New Item is disabled."))
		if not new_item.is_stock_item:
			frappe.throw(_("New Item must be a stock item."))


@frappe.whitelist()
def generate_preview(docname: str):
	doc = frappe.get_doc("BOM Item Replacement Tool", docname)
	doc.check_permission("write")
	return doc.generate_preview()


@frappe.whitelist()
def enqueue_replacement(docname: str):
	doc = frappe.get_doc("BOM Item Replacement Tool", docname)
	doc.check_permission("write")
	return doc.enqueue_replacement()


def execute_replacement(docname: str):
	doc = frappe.get_doc("BOM Item Replacement Tool", docname)
	doc.status = "Processing"
	doc.save(ignore_permissions=True)
	frappe.db.commit()

	updated = skipped = failed = 0
	try:
		for row in doc.replacement_details:
			if row.status == "Updated":
				continue
			if doc.dry_run:
				row.status = "Skipped"
				row.error = _("Dry run: no changes made.")
				skipped += 1
				continue

			try:
				new_bom_name = replace_item_in_bom(doc, row.bom)
				row.old_bom = row.bom
				row.new_bom = new_bom_name
				row.status = "Updated"
				row.error = None
				updated += 1
			except SubmittedWorkOrderError as exc:
				row.status = "Skipped"
				row.old_bom = row.bom
				row.error = str(exc)
				skipped += 1
			except Exception:
				frappe.db.rollback()
				row.status = "Failed"
				row.old_bom = row.bom
				row.error = frappe.get_traceback()
				failed += 1
			else:
				frappe.db.commit()

		doc.status = "Completed" if not failed else "Failed"
		doc.save(ignore_permissions=True)
		frappe.db.commit()
		message = _(
			"Successfully replaced {0} with {1} in {2} BOMs.\n{3} BOMs skipped.\n{4} BOMs failed."
		).format(doc.old_item, doc.new_item, updated, skipped, failed)
		doc.add_comment("Info", message)
		return {"updated": updated, "skipped": skipped, "failed": failed}
	except Exception:
		frappe.db.rollback()
		doc.db_set("status", "Failed", update_modified=True)
		raise


class SubmittedWorkOrderError(frappe.ValidationError):
	pass


def replace_item_in_bom(doc: BOMItemReplacementTool, bom_name: str) -> str:
	if frappe.db.exists("Work Order", {"bom_no": bom_name, "docstatus": ["!=", 2]}):
		raise SubmittedWorkOrderError(_("Open or submitted Work Orders exist for BOM {0}.").format(bom_name))

	old_bom = frappe.get_doc("BOM", bom_name)
	old_bom.cancel()

	new_bom = frappe.copy_doc(old_bom)
	new_bom.name = None
	new_bom.amended_from = old_bom.name
	new_bom.docstatus = 0
	new_bom.is_active = 1
	new_bom.is_default = old_bom.is_default

	new_item = frappe.get_cached_doc("Item", doc.new_item)
	for item_row in new_bom.items:
		if item_row.item_code == doc.old_item:
			item_row.item_code = doc.new_item
			item_row.item_name = new_item.item_name
			item_row.description = new_item.description
			item_row.stock_uom = new_item.stock_uom

	new_bom.set_rate_of_sub_assembly_item()
	new_bom.calculate_cost()
	new_bom.insert(ignore_permissions=True)
	new_bom.submit()

	if doc.update_parent_boms:
		from erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool import replace_bom

		replace_bom(old_bom.name, new_bom.name)

	if doc.update_draft_work_orders:
		update_draft_work_orders(old_bom.name, new_bom.name)

	return new_bom.name


def update_draft_work_orders(old_bom: str, new_bom: str):
	for work_order_name in frappe.get_all("Work Order", filters={"bom_no": old_bom, "docstatus": 0}, pluck="name"):
		work_order = frappe.get_doc("Work Order", work_order_name)
		work_order.bom_no = new_bom
		work_order.save(ignore_permissions=True)
