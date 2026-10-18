# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Item Alternative transfer: both directions, skip rules, per-record rollback.

	bench --site <site> run-tests --module playground.playground.item_consolidation.tests.test_actions

frappe.get_all / get_doc / db are faked; nothing is read or written.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from playground.playground.item_consolidation import actions


class FakeAlternative(frappe._dict):
	"""Stands in for an Item Alternative doc: set() / save(), optional save error."""

	def set(self, field, value):
		self[field] = value

	def save(self):
		if self.get("save_error"):
			raise frappe.ValidationError(self.save_error)
		self.saved = True


def alternative(name, item_code, alternative_item_code, two_way=0, save_error=None):
	return FakeAlternative(
		name=name,
		item_code=item_code,
		alternative_item_code=alternative_item_code,
		two_way=two_way,
		save_error=save_error,
	)


class TransferItemAlternativesTest(unittest.TestCase):
	def transfer(self, records, existing=()):
		"""``existing``: (item_code, alternative_item_code, two_way) rows already on file."""
		by_name = {r.name: r for r in records}

		def exists(doctype, filters):
			for item_code, alt_code, two_way in existing:
				if (item_code, alt_code) == (filters["item_code"], filters["alternative_item_code"]):
					if "two_way" not in filters or filters["two_way"] == two_way:
						return True
			return False

		db = MagicMock()
		db.exists.side_effect = exists
		db.get_value.return_value = "Kept Widget"
		with patch.object(actions.frappe, "get_all", return_value=list(by_name)), patch.object(
			actions.frappe, "get_doc", side_effect=lambda doctype, name: by_name[name]
		), patch.object(actions.frappe, "db", db):
			moved, skipped = actions.transfer_item_alternatives("OLD", "KEEP")
		return moved, skipped, db

	def test_moves_both_directions(self):
		as_item = alternative("ALT-1", "OLD", "X")
		as_alternative = alternative("ALT-2", "Y", "OLD")
		moved, skipped, _db = self.transfer([as_item, as_alternative])

		self.assertEqual(skipped, [])
		self.assertEqual(moved, ["KEEP → X", "Y → KEEP"])
		self.assertEqual((as_item.item_code, as_item.item_name), ("KEEP", "Kept Widget"))
		self.assertEqual(
			(as_alternative.alternative_item_code, as_alternative.alternative_item_name), ("KEEP", "Kept Widget")
		)
		self.assertTrue(as_item.saved and as_alternative.saved)

	def test_skips_self_reference(self):
		# OLD -> KEEP would become KEEP -> KEEP.
		record = alternative("ALT-1", "OLD", "KEEP")
		moved, skipped, _db = self.transfer([record])
		self.assertEqual(moved, [])
		self.assertEqual(len(skipped), 1)
		self.assertEqual(record.item_code, "OLD")

	def test_skips_existing_and_two_way_reverse(self):
		same = alternative("ALT-1", "OLD", "X")
		covered = alternative("ALT-2", "Y", "OLD")
		moved, skipped, _db = self.transfer(
			[same, covered], existing=[("KEEP", "X", 0), ("KEEP", "Y", 1)]
		)
		self.assertEqual(moved, [])
		self.assertEqual(len(skipped), 2)
		self.assertNotIn("saved", same)

	def test_one_way_reverse_does_not_count_as_existing(self):
		record = alternative("ALT-1", "Y", "OLD")
		moved, _skipped, _db = self.transfer([record], existing=[("KEEP", "Y", 0)])
		self.assertEqual(moved, ["Y → KEEP"])

	def test_validation_error_rolls_back_only_that_record(self):
		bad = alternative("ALT-1", "OLD", "X", save_error="Not allow to set alternative item")
		good = alternative("ALT-2", "Y", "OLD")
		moved, skipped, db = self.transfer([bad, good])

		db.rollback.assert_called_once_with(save_point="item_consolidation_alternative")
		self.assertEqual(moved, ["Y → KEEP"])
		self.assertEqual(len(skipped), 1)
		self.assertIn("ALT-1", skipped[0])
		self.assertIn("Not allow", skipped[0])
