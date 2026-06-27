# Copyright (c) 2026, Playground and contributors
# For license information, please see license.txt

from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from playground.manufacturing.doctype.bom_item_replacement_tool.bom_item_replacement_tool import (
	BOMItemReplacementTool,
	SubmittedWorkOrderError,
	execute_replacement,
	replace_item_in_bom,
	update_draft_work_orders,
)


class TestBOMItemReplacementTool(FrappeTestCase):
	def test_single_bom_replacement_uses_amendment_and_preserves_qty(self):
		doc = Mock(old_item="OLD", new_item="NEW", update_parent_boms=True, update_draft_work_orders=False)
		old_bom = Mock(name="BOM-OLD", is_default=1)
		old_bom.items = [Mock(item_code="OLD", qty=5, source_warehouse="Stores")]
		new_bom = Mock(name="BOM-NEW")
		new_bom.items = [Mock(item_code="OLD", qty=5, source_warehouse="Stores")]
		new_item = Mock(item_name="New Item", description="New Desc", stock_uom="Nos")

		with patch.object(frappe.db, "exists", return_value=False), patch("frappe.get_doc", return_value=old_bom), patch(
			"frappe.copy_doc", return_value=new_bom
		), patch("frappe.get_cached_doc", return_value=new_item), patch(
			"erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool.replace_bom"
		) as replace_bom:
			result = replace_item_in_bom(doc, "BOM-OLD")

		self.assertEqual(result, "BOM-NEW")
		old_bom.cancel.assert_called_once()
		self.assertEqual(new_bom.items[0].item_code, "NEW")
		self.assertEqual(new_bom.items[0].qty, 5)
		replace_bom.assert_called_once_with("BOM-OLD", "BOM-NEW")

	def test_multiple_bom_replacement_counts_successes(self):
		doc = Mock(status="Preview Generated", old_item="OLD", new_item="NEW", dry_run=False)
		doc.replacement_details = [Mock(bom="BOM-1", status="Pending"), Mock(bom="BOM-2", status="Pending")]
		with patch("frappe.get_doc", return_value=doc), patch(
			"playground.manufacturing.doctype.bom_item_replacement_tool.bom_item_replacement_tool.replace_item_in_bom",
			side_effect=["BOM-1-NEW", "BOM-2-NEW"],
		), patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"):
			result = execute_replacement("BOM-REPLACE-2026-00001")

		self.assertEqual(result["updated"], 2)
		self.assertEqual(doc.replacement_details[0].new_bom, "BOM-1-NEW")
		self.assertEqual(doc.replacement_details[1].new_bom, "BOM-2-NEW")

	def test_nested_bom_replacement_updates_parent_references(self):
		doc = Mock(old_item="OLD", new_item="NEW", update_parent_boms=True, update_draft_work_orders=False)
		old_bom = Mock(name="SUB-BOM", is_default=0, items=[])
		new_bom = Mock(name="SUB-BOM-NEW", items=[])
		with patch.object(frappe.db, "exists", return_value=False), patch("frappe.get_doc", return_value=old_bom), patch(
			"frappe.copy_doc", return_value=new_bom
		), patch("frappe.get_cached_doc"), patch(
			"erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool.replace_bom"
		) as replace_bom:
			replace_item_in_bom(doc, "SUB-BOM")

		replace_bom.assert_called_once_with("SUB-BOM", "SUB-BOM-NEW")

	def test_existing_work_orders_skip_bom(self):
		doc = Mock(old_item="OLD", new_item="NEW")
		with patch.object(frappe.db, "exists", return_value=True):
			with self.assertRaises(SubmittedWorkOrderError):
				replace_item_in_bom(doc, "BOM-OLD")

	def test_dry_run_mode_does_not_replace(self):
		doc = Mock(status="Preview Generated", old_item="OLD", new_item="NEW", dry_run=True)
		doc.replacement_details = [Mock(bom="BOM-1", status="Pending")]
		with patch("frappe.get_doc", return_value=doc), patch(
			"playground.manufacturing.doctype.bom_item_replacement_tool.bom_item_replacement_tool.replace_item_in_bom"
		) as replace_item, patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback"):
			result = execute_replacement("BOM-REPLACE-2026-00001")

		replace_item.assert_not_called()
		self.assertEqual(result["skipped"], 1)

	def test_failed_replacement_rolls_back_and_marks_failed(self):
		doc = Mock(status="Preview Generated", old_item="OLD", new_item="NEW", dry_run=False)
		doc.replacement_details = [Mock(bom="BOM-1", status="Pending")]
		with patch("frappe.get_doc", return_value=doc), patch(
			"playground.manufacturing.doctype.bom_item_replacement_tool.bom_item_replacement_tool.replace_item_in_bom",
			side_effect=Exception("boom"),
		), patch.object(frappe.db, "commit"), patch.object(frappe.db, "rollback") as rollback:
			result = execute_replacement("BOM-REPLACE-2026-00001")

		rollback.assert_called()
		self.assertEqual(result["failed"], 1)
		self.assertEqual(doc.replacement_details[0].status, "Failed")

	def test_parent_bom_update_validation_can_be_disabled(self):
		doc = Mock(old_item="OLD", new_item="NEW", update_parent_boms=False, update_draft_work_orders=False)
		old_bom = Mock(name="BOM-OLD", is_default=0, items=[])
		new_bom = Mock(name="BOM-NEW", items=[])
		with patch.object(frappe.db, "exists", return_value=False), patch("frappe.get_doc", return_value=old_bom), patch(
			"frappe.copy_doc", return_value=new_bom
		), patch("frappe.get_cached_doc"), patch(
			"erpnext.manufacturing.doctype.bom_update_tool.bom_update_tool.replace_bom"
		) as replace_bom:
			replace_item_in_bom(doc, "BOM-OLD")

		replace_bom.assert_not_called()

	def test_update_draft_work_orders_only_touches_drafts(self):
		work_order = Mock()
		with patch("frappe.get_all", return_value=["WO-DRAFT"]), patch("frappe.get_doc", return_value=work_order) as get_doc:
			update_draft_work_orders("BOM-OLD", "BOM-NEW")

		get_doc.assert_called_once_with("Work Order", "WO-DRAFT")
		self.assertEqual(work_order.bom_no, "BOM-NEW")
		work_order.save.assert_called_once_with(ignore_permissions=True)

	def test_generate_preview_populates_child_rows(self):
		doc = BOMItemReplacementTool()
		doc.old_item = "OLD"
		doc.new_item = "NEW"
		doc.only_active_boms = 1
		with patch.object(frappe.db, "sql", return_value=[frappe._dict(name="BOM-1", item="FG", qty=2)]), patch.object(
			doc, "save"
		):
			count = doc.generate_preview()

		self.assertEqual(count, 1)
		self.assertEqual(doc.status, "Preview Generated")
		self.assertEqual(doc.replacement_details[0].bom, "BOM-1")
