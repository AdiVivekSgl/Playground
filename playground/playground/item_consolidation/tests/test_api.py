# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Job flow: each pair is isolated, failures roll back only that pair.

	bench --site <site> run-tests --module playground.playground.item_consolidation.tests.test_api

The steps in actions.py and every DB call are mocked; nothing is written.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from playground.playground.item_consolidation import api


def row(name, old, keep, status="Pending"):
	return frappe._dict(name=name, idx=int(name[-1]), old_item=old, keep_item=keep, status=status)


def fake_doc(rows, **flags):
	doc = MagicMock()
	doc.name = "ICN-00001"
	doc.company = "Frontec"
	doc.items = rows
	doc.copy_masters = flags.get("copy_masters", 1)
	doc.replace_in_boms = flags.get("replace_in_boms", 1)
	doc.update_bom_costs = flags.get("update_bom_costs", 1)
	doc.disable_old_items = flags.get("disable_old_items", 1)
	doc.disable_despite_open_documents = flags.get("disable_despite_open_documents", 0)
	return doc


def clean_ctx(boms=()):
	return frappe._dict(
		blockers=[],
		warnings=["a warning"],
		stock=[frappe._dict(warehouse="Stores", actual_qty=2, stock_value=20)],
		old=frappe._dict(stock_uom="Nos"),
		boms=[frappe._dict(bom=b) for b in boms],
	)


class OverallStatusTest(unittest.TestCase):
	def test_statuses(self):
		self.assertEqual(api.overall_status(["Done", "Done"]), "Completed")
		self.assertEqual(api.overall_status(["Done", "Failed"]), "Partly Failed")
		self.assertEqual(api.overall_status(["Failed"]), "Failed")
		self.assertEqual(api.overall_status([]), "Failed")


class ConsolidatePairTest(unittest.TestCase):
	def test_runs_enabled_steps_and_reports(self):
		doc = fake_doc([])
		with patch.object(api.checks, "check_pair", return_value=clean_ctx(["BOM-1"])), patch.object(
			api, "actions"
		) as actions:
			actions.move_stock.return_value = ["SE-1"]
			actions.copy_masters.return_value = (["1 Item Price(s)"], ["Item Alternative ALT-1 not moved: x"])
			actions.replace_in_boms.return_value = (["BOM-1"], ["BOM-2: reason"])
			result = api.consolidate_pair(doc, row("r1", "OLD", "KEEP"))

		actions.disable_item.assert_called_once_with("OLD", "KEEP", "ICN-00001", open_documents=None)
		self.assertEqual(result["stock_entries"], "SE-1")
		self.assertEqual(result["boms_updated"], "BOM-1")
		self.assertIn("a warning", result["notes"])
		self.assertIn("BOM-2", result["notes"])
		self.assertIn("ALT-1", result["notes"])

	def test_disabled_steps_are_skipped(self):
		doc = fake_doc([], copy_masters=0, replace_in_boms=0, disable_old_items=0)
		with patch.object(api.checks, "check_pair", return_value=clean_ctx()), patch.object(
			api, "actions"
		) as actions:
			actions.move_stock.return_value = []
			result = api.consolidate_pair(doc, row("r1", "OLD", "KEEP"))
		actions.copy_masters.assert_not_called()
		actions.replace_in_boms.assert_not_called()
		actions.disable_item.assert_not_called()
		self.assertEqual(result["boms_updated"], "")

	def test_blocker_raises_before_any_step(self):
		ctx = clean_ctx()
		ctx.blockers = ["Stock UOMs differ"]
		with patch.object(api.checks, "check_pair", return_value=ctx), patch.object(api, "actions") as actions:
			with self.assertRaises(frappe.ValidationError):
				api.consolidate_pair(fake_doc([]), row("r1", "OLD", "KEEP"))
		actions.move_stock.assert_not_called()


class RunConsolidationTest(unittest.TestCase):
	def run_job(self, rows, outcomes, row_statuses):
		"""outcomes: old_item -> result dict, or an Exception to raise."""
		doc = fake_doc(rows)

		def consolidate(_doc, r):
			out = outcomes[r.old_item]
			if isinstance(out, Exception):
				raise out
			return out

		db = MagicMock()
		with patch.object(api.frappe, "get_doc", return_value=doc), patch.object(
			api.frappe, "db", db
		), patch.object(api.frappe, "get_all", return_value=row_statuses), patch.object(
			api.frappe, "log_error"
		), patch.object(api, "consolidate_pair", side_effect=consolidate), patch.object(
			api.bom_cost, "update_cost_for_all_boms"
		) as recost:
			api.run_consolidation("ICN-00001")
		return doc, db, recost

	def test_failure_rolls_back_only_that_pair(self):
		ok = {"stock_entries": "SE-1", "boms_updated": "BOM-1", "masters_updated": "", "notes": ""}
		rows = [row("r1", "A", "K"), row("r2", "B", "K"), row("r3", "C", "K", status="Done")]
		doc, db, recost = self.run_job(rows, {"A": ok, "B": Exception("boom")}, ["Done", "Failed", "Done"])

		db.rollback.assert_called_once_with(save_point="item_consolidation_pair")
		updates = {c.args[1]: c.args[2] for c in db.set_value.call_args_list}
		self.assertEqual(updates["r1"]["status"], "Done")
		self.assertEqual(updates["r2"], {"status": "Failed", "error": "boom"})
		self.assertNotIn("r3", updates)  # already Done -> not re-run
		doc.db_set.assert_any_call("status", "Partly Failed")
		recost.assert_called_once()

	def test_no_bom_change_skips_recost(self):
		ok = {"stock_entries": "", "boms_updated": "", "masters_updated": "", "notes": ""}
		doc, _db, recost = self.run_job([row("r1", "A", "K")], {"A": ok}, ["Done"])
		doc.db_set.assert_any_call("status", "Completed")
		recost.assert_not_called()

	def test_override_passes_open_documents_to_disable(self):
		docs = [{"doctype": "Stock Entry", "names": ["MAT-STE-1"]}]
		ctx = clean_ctx()
		ctx.open_documents = docs
		doc = fake_doc([], disable_despite_open_documents=1)
		with patch.object(api.checks, "check_pair", return_value=ctx) as check, patch.object(
			api, "actions"
		) as actions:
			actions.move_stock.return_value = []
			actions.copy_masters.return_value = ([], [])
			actions.replace_in_boms.return_value = ([], [])
			api.consolidate_pair(doc, row("r1", "OLD", "KEEP"))
		self.assertFalse(check.call_args.kwargs["block_open_documents"])
		actions.disable_item.assert_called_once_with("OLD", "KEEP", "ICN-00001", open_documents=docs)


class BlockOpenDocumentsTest(unittest.TestCase):
	def test_only_when_disabling_without_override(self):
		self.assertTrue(api.block_open_documents(fake_doc([], disable_old_items=1)))
		self.assertFalse(
			api.block_open_documents(fake_doc([], disable_old_items=1, disable_despite_open_documents=1))
		)
		self.assertFalse(api.block_open_documents(fake_doc([], disable_old_items=0)))
