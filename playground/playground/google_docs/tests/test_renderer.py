# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Renderer tests.

The Google Docs API calls (docs.get_document / docs.batch_update) are mocked, but
the *real* request builders and index math run, so these assert the exact
batchUpdate requests the engine would send — including the highest-index-first
ordering that keeps edits from corrupting each other.
"""

import unittest
from unittest.mock import patch

import frappe

from playground.playground.google_docs import docs, renderer


def _para(start, text):
	end = start + len(text)
	return {
		"startIndex": start,
		"endIndex": end,
		"paragraph": {"elements": [{"startIndex": start, "endIndex": end, "textRun": {"content": text}}]},
	}


def _collect_batches():
	"""Return (calls_list, side_effect) where each batch_update appends its requests."""
	calls = []

	def _batch(document_id, requests):
		calls.append(requests)
		return {}

	return calls, _batch


class TestScalarRender(unittest.TestCase):
	def setUp(self):
		self.template = frappe._dict(
			field_mappings=[
				frappe._dict(erp_fieldname="supplier_name", field_type="Data", google_placeholder=""),
			],
			table_mappings=[],
			column_mappings=[],
		)
		self.doc = frappe._dict(
			doctype="Purchase Order", name="PO-1", currency="INR", supplier_name="ABC",
		)

	def test_create_replaces_placeholder_and_marks_named_range(self):
		document = {"body": {"content": [_para(1, "Supplier: {{supplier_name}}\n")]}, "namedRanges": {}}
		calls, batch = _collect_batches()
		with patch.object(docs, "get_document", return_value=document), patch.object(
			docs, "batch_update", side_effect=batch
		):
			renderer.render(self.template, self.doc, "docid", is_update=False)

		self.assertEqual(len(calls), 1)
		reqs = calls[0]
		# {{supplier_name}} starts at index 11 (after "Supplier: "), length 17.
		self.assertEqual(reqs[0]["deleteContentRange"]["range"], {"startIndex": 11, "endIndex": 28})
		self.assertEqual(reqs[1]["insertText"], {"location": {"index": 11}, "text": "ABC"})
		self.assertEqual(
			reqs[2]["createNamedRange"],
			{"name": "erpgdoc:field:supplier_name", "range": {"startIndex": 11, "endIndex": 14}},
		)

	def test_update_rewrites_named_range_only(self):
		document = {
			"body": {"content": [_para(1, "Supplier: ABC\n")]},
			"namedRanges": {
				"erpgdoc:field:supplier_name": {
					"namedRanges": [{"ranges": [{"startIndex": 11, "endIndex": 14}]}]
				}
			},
		}
		self.doc.supplier_name = "XYZ"
		calls, batch = _collect_batches()
		with patch.object(docs, "get_document", return_value=document), patch.object(
			docs, "batch_update", side_effect=batch
		):
			renderer.render(self.template, self.doc, "docid", is_update=True)

		reqs = calls[0]
		self.assertEqual(reqs[0], {"deleteNamedRange": {"name": "erpgdoc:field:supplier_name"}})
		self.assertEqual(reqs[1]["deleteContentRange"]["range"], {"startIndex": 11, "endIndex": 14})
		self.assertEqual(reqs[2]["insertText"], {"location": {"index": 11}, "text": "XYZ"})

	def test_missing_required_placeholder_raises(self):
		self.template.field_mappings[0].required = 1
		document = {"body": {"content": [_para(1, "No placeholder here\n")]}, "namedRanges": {}}
		with patch.object(docs, "get_document", return_value=document), patch.object(
			docs, "batch_update", return_value={}
		):
			with self.assertRaises(Exception):
				renderer.render(self.template, self.doc, "docid", is_update=False)

	def test_multiple_scalars_applied_high_index_first(self):
		self.template.field_mappings = [
			frappe._dict(erp_fieldname="a", field_type="Data"),
			frappe._dict(erp_fieldname="b", field_type="Data"),
		]
		self.doc.a = "AA"
		self.doc.b = "BB"
		document = {"body": {"content": [_para(1, "{{a}} then {{b}}\n")]}, "namedRanges": {}}
		calls, batch = _collect_batches()
		with patch.object(docs, "get_document", return_value=document), patch.object(
			docs, "batch_update", side_effect=batch
		):
			renderer.render(self.template, self.doc, "docid", is_update=False)
		reqs = calls[0]
		# {{b}} sits at a higher index than {{a}}, so it must be edited first.
		first_delete = reqs[0]["deleteContentRange"]["range"]["startIndex"]
		later_delete = reqs[3]["deleteContentRange"]["range"]["startIndex"]
		self.assertGreater(first_delete, later_delete)


class TestTableRender(unittest.TestCase):
	def _table_doc(self):
		# One inserted 2x2 table; cell text-insertion indexes are 3, 6, 9, 12.
		cells_row1 = [
			{"startIndex": 2, "content": [_para(3, "")]},
			{"startIndex": 5, "content": [_para(6, "")]},
		]
		cells_row2 = [
			{"startIndex": 8, "content": [_para(9, "")]},
			{"startIndex": 11, "content": [_para(12, "")]},
		]
		table_el = {
			"startIndex": 1,
			"endIndex": 40,
			"table": {"tableRows": [{"tableCells": cells_row1}, {"tableCells": cells_row2}]},
		}
		return {"body": {"content": [table_el]}, "namedRanges": {}}

	def test_create_table_from_marker(self):
		template = frappe._dict(
			field_mappings=[],
			table_mappings=[
				frappe._dict(child_table_fieldname="items", table_marker="{{items_table}}"),
			],
			column_mappings=[
				frappe._dict(table_marker="{{items_table}}", child_fieldname="item_code", column_label="Item Code", idx=1),
				frappe._dict(table_marker="{{items_table}}", child_fieldname="qty", column_label="Qty", field_type="Float", idx=2),
			],
		)
		doc = frappe._dict(
			doctype="Purchase Order", name="PO-1", currency="INR",
			items=[frappe._dict(item_code="ABC-001", qty=100)],
		)
		marker_doc = {"body": {"content": [_para(1, "{{items_table}}\n")]}, "namedRanges": {}}
		table_doc = self._table_doc()
		calls, batch = _collect_batches()
		# get_document sequence: marker lookup, post-insert fill lookup, post-fill width lookup.
		with patch.object(docs, "get_document", side_effect=[marker_doc, table_doc, table_doc]), patch.object(
			docs, "batch_update", side_effect=batch
		):
			renderer.render(template, doc, "docid", is_update=False)

		# 1: delete marker text; 2: insertTable; 3: cell fills; 4: create named range.
		self.assertEqual(calls[0][0]["deleteContentRange"]["range"], {"startIndex": 1, "endIndex": 16})
		self.assertEqual(calls[1][0]["insertTable"], {"location": {"index": 1}, "rows": 2, "columns": 2})

		fills = calls[2]
		# Filled highest-index-first: 12 -> "100", 9 -> "ABC-001", 6 -> "Qty", 3 -> "Item Code".
		self.assertEqual(fills[0]["insertText"], {"location": {"index": 12}, "text": "100"})
		self.assertEqual(fills[1]["insertText"], {"location": {"index": 9}, "text": "ABC-001"})
		self.assertEqual(fills[2]["insertText"], {"location": {"index": 6}, "text": "Qty"})
		self.assertEqual(fills[3]["insertText"], {"location": {"index": 3}, "text": "Item Code"})

		self.assertEqual(
			calls[-1][0]["createNamedRange"],
			{"name": "erpgdoc:table:items", "range": {"startIndex": 1, "endIndex": 40}},
		)

	def test_hidden_column_skipped(self):
		template = frappe._dict(
			field_mappings=[],
			table_mappings=[frappe._dict(child_table_fieldname="items", table_marker="{{t}}")],
			column_mappings=[
				frappe._dict(table_marker="{{t}}", child_fieldname="item_code", idx=1),
				frappe._dict(table_marker="{{t}}", child_fieldname="secret", idx=2, hidden=1),
			],
		)
		cols = renderer._columns_for(template, template.table_mappings[0])
		self.assertEqual([c.child_fieldname for c in cols], ["item_code"])
