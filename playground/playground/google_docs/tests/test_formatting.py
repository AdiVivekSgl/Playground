# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Unit tests for field-type-aware value formatting."""

import unittest

from playground.playground.google_docs import formatting


class TestFormatting(unittest.TestCase):
	def test_data_passthrough(self):
		self.assertEqual(formatting.format_value("ABC Suppliers", "Data"), "ABC Suppliers")

	def test_blank_uses_default_not_none(self):
		self.assertEqual(formatting.format_value(None, "Data"), "")
		self.assertEqual(formatting.format_value(None, "Data", default="N/A"), "N/A")
		self.assertEqual(formatting.format_value("", "Currency", default="-"), "-")

	def test_int(self):
		self.assertEqual(formatting.format_value(100, "Int"), "100")
		self.assertEqual(formatting.format_value("50", "Int"), "50")

	def test_float_trims_trailing_zero_but_keeps_decimals(self):
		self.assertEqual(formatting.format_value(100.0, "Float"), "100")
		self.assertEqual(formatting.format_value(12.5, "Float"), "12.5")

	def test_check(self):
		self.assertEqual(formatting.format_value(1, "Check"), "Yes")
		self.assertEqual(formatting.format_value(0, "Check"), "No")

	def test_currency_has_symbol_and_separators(self):
		out = formatting.format_value(5000, "Currency", currency="INR")
		# Exact symbol depends on system number format; assert the digits grouped.
		self.assertIn("5,000", out)

	def test_date(self):
		# formatdate respects the user/system format; just assert it renders a year.
		self.assertIn("2026", formatting.format_value("2026-09-09", "Date"))

	def test_text_editor_strips_html(self):
		self.assertEqual(
			formatting.format_value("<b>Net 30</b> days", "Text Editor"),
			"Net 30 days",
		)

	def test_transform(self):
		self.assertEqual(formatting.format_value("abc", "Data", transform="Uppercase"), "ABC")
		self.assertEqual(formatting.format_value("ABC", "Data", transform="Lowercase"), "abc")
		self.assertEqual(
			formatting.format_value("abc suppliers", "Data", transform="Title Case"),
			"Abc Suppliers",
		)
