# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Unit tests for placeholder normalisation, named-range names and file naming."""

import unittest

import frappe

from playground.playground.google_docs import placeholders


class TestPlaceholders(unittest.TestCase):
	def test_normalise_from_bare_token(self):
		self.assertEqual(placeholders.normalise_placeholder("supplier_name"), "{{supplier_name}}")

	def test_normalise_keeps_wrapped(self):
		self.assertEqual(placeholders.normalise_placeholder("{{x}}"), "{{x}}")

	def test_normalise_defaults_to_fieldname(self):
		self.assertEqual(placeholders.normalise_placeholder("", "grand_total"), "{{grand_total}}")

	def test_range_names(self):
		self.assertEqual(placeholders.field_range_name("supplier_name"), "erpgdoc:field:supplier_name")
		self.assertEqual(placeholders.table_range_name("items"), "erpgdoc:table:items")

	def test_render_name(self):
		doc = frappe._dict(doctype="Purchase Order", name="KPX-PO-00045", supplier_name="ABC Suppliers")
		out = placeholders.render_name("{{doctype}} - {{name}} - {{supplier_name}}", doc)
		self.assertEqual(out, "Purchase Order - KPX-PO-00045 - ABC Suppliers")

	def test_render_name_collapses_missing_token(self):
		doc = frappe._dict(doctype="Purchase Order", name="PO-1", supplier_name=None)
		out = placeholders.render_name("{{doctype}} - {{name}} - {{supplier_name}}", doc)
		self.assertEqual(out, "Purchase Order - PO-1")

	def test_sanitize_strips_illegal_chars(self):
		self.assertEqual(placeholders.sanitize_filename("a/b:c*?"), "a b c")

	def test_sanitize_never_empty(self):
		self.assertEqual(placeholders.sanitize_filename("///"), "Document")
