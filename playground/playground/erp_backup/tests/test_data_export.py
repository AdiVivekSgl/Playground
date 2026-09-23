# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""CSV export tests (spec 1C, 19).

Consistent, parent-preserving naming and an export list that honours the
configurable enabled/disabled flags.
"""

import unittest

import frappe

from playground.playground.erp_backup import data_export


class TestCsvNaming(unittest.TestCase):
	def test_child_table_naming(self):
		self.assertEqual(data_export._csv_name("Sales Order Item"), "Sales_Order_Item.csv")
		self.assertEqual(data_export._csv_name("Payment Entry Reference"), "Payment_Entry_Reference.csv")

	def test_simple_naming(self):
		self.assertEqual(data_export._csv_name("BOM"), "BOM.csv")
		self.assertEqual(data_export._csv_name("Customer"), "Customer.csv")

	def test_unsafe_characters_replaced(self):
		self.assertEqual(data_export._csv_name("Weird/Type:Name"), "Weird_Type_Name.csv")


class TestEnabledDoctypes(unittest.TestCase):
	def test_filters_disabled_and_dedupes(self):
		settings = frappe._dict(export_doctypes=[
			frappe._dict(document_type="Customer", enabled=1),
			frappe._dict(document_type="Customer", enabled=1),   # duplicate
			frappe._dict(document_type="Supplier", enabled=0),   # disabled
			frappe._dict(document_type="  Item ", enabled=1),    # trimmed
			frappe._dict(document_type="", enabled=1),           # empty ignored
		])
		self.assertEqual(data_export._enabled_doctypes(settings), ["Customer", "Item"])

	def test_empty_list(self):
		self.assertEqual(data_export._enabled_doctypes(frappe._dict(export_doctypes=[])), [])


if __name__ == "__main__":
	unittest.main()
