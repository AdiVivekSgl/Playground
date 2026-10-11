# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""`stock` command tests: mapping required, runs as the mapped user, session restored.

	bench --site <site> run-tests --module playground.playground.whatsapp.tests.test_stock

frappe.set_user / has_permission / get_list are faked, so no data is read and
the real session is never switched.
"""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from playground.playground.whatsapp import stock, user_context
from playground.playground.whatsapp.phone import Sender

USER = "ravi@frontec.in"
MAPPED = Sender(phone="919812345678", user=USER)
UNMAPPED = Sender(phone="919812345678", user=None)

ITEM = frappe._dict(name="XYZ-123", item_name="Widget Blue", stock_uom="Nos", is_stock_item=1, disabled=0)


def bin_row(warehouse, actual, projected=None):
	return frappe._dict(warehouse=warehouse, actual_qty=actual, projected_qty=actual if projected is None else projected)


class FakeFrappe:
	"""Records which user each permission check / query ran as."""

	def __init__(self, items=(ITEM,), bins=(), hidden_warehouses=(), denied_doctypes=(), get_list_error=None):
		self.session = SimpleNamespace(user="Guest")
		self.items = list(items)
		self.bins = list(bins)
		self.hidden_warehouses = set(hidden_warehouses)
		self.denied_doctypes = set(denied_doctypes)
		self.get_list_error = get_list_error
		self.set_user_calls = []
		self.queries = []  # (doctype, session user at the time)

	def set_user(self, user):
		self.set_user_calls.append(user)
		self.session.user = user

	def has_permission(self, doctype, ptype="read", doc=None, user=None, *a, **k):
		self.queries.append((f"has_permission:{doctype}", self.session.user, user))
		return doctype not in self.denied_doctypes

	def get_list(self, doctype, filters=None, fields=None, pluck=None, **kwargs):
		self.queries.append((doctype, self.session.user, None))
		if self.get_list_error:
			raise self.get_list_error
		if doctype == "Item":
			return [i for i in self.items if i.name.casefold() == filters["name"].casefold()][:1]
		if doctype == "Bin":
			return [b for b in self.bins if b.get("item_code", ITEM.name) == filters["item_code"]]
		if doctype == "Warehouse":
			return [w for w in filters["name"][1] if w not in self.hidden_warehouses]
		raise AssertionError(f"unexpected get_list on {doctype}")


class StockTestCase(unittest.TestCase):
	@contextmanager
	def fake(self, **kwargs):
		fake = FakeFrappe(**kwargs)
		with patch.object(frappe, "session", fake.session), \
			patch.object(frappe, "set_user", fake.set_user), \
			patch.object(frappe, "has_permission", fake.has_permission), \
			patch.object(frappe, "get_list", fake.get_list):
			yield fake

	def assert_ran_as_user_and_restored(self, fake):
		self.assertEqual(fake.set_user_calls, [USER, "Guest"])
		self.assertEqual(fake.session.user, "Guest")
		for name, session_user, checked_user in fake.queries:
			self.assertEqual(session_user, USER, name)
			if checked_user is not None:
				self.assertEqual(checked_user, USER, name)


class TestStockAuthorized(StockTestCase):
	def test_stock_per_warehouse(self):
		bins = [bin_row("Stores - FT", 100, 80), bin_row("WIP - FT", 20.5)]
		with self.fake(bins=bins) as fake:
			reply = stock.get_stock("XYZ-123", MAPPED)
		self.assertEqual(
			reply,
			"📦 *XYZ-123* - Widget Blue\n"
			"In stock: *120.5* Nos\n"
			"\n"
			"• Stores - FT: 100 (projected 80)\n"
			"• WIP - FT: 20.5",
		)
		self.assert_ran_as_user_and_restored(fake)
		self.assertIn(("has_permission:Bin", USER, USER), fake.queries)

	def test_canonical_code_and_trailing_punctuation(self):
		with self.fake(bins=[bin_row("Stores - FT", 5)]):
			reply = stock.get_stock("  xyz-123? ", MAPPED)
		self.assertTrue(reply.startswith("📦 *XYZ-123*"), reply)

	def test_zero_bins_are_skipped(self):
		with self.fake(bins=[bin_row("Stores - FT", 0, 0)]):
			reply = stock.get_stock("XYZ-123", MAPPED)
		self.assertIn("No stock in any warehouse you can see", reply)

	def test_warehouses_the_user_cannot_read_are_hidden(self):
		bins = [bin_row("Stores - FT", 100), bin_row("Secret - FT", 999)]
		with self.fake(bins=bins, hidden_warehouses={"Secret - FT"}):
			reply = stock.get_stock("XYZ-123", MAPPED)
		self.assertNotIn("Secret", reply)
		self.assertNotIn("999", reply)
		self.assertIn("In stock: *100* Nos", reply)

	def test_long_output_is_capped(self):
		bins = [bin_row(f"WH {i:02d} - FT", 1000 - i) for i in range(25)]
		with self.fake(bins=bins):
			reply = stock.get_stock("XYZ-123", MAPPED)
		self.assertEqual(reply.count("• "), stock.MAX_WAREHOUSES_LISTED)
		self.assertTrue(reply.endswith("…and 15 more warehouses"), reply)
		# The total still covers every visible warehouse, not just the listed ones.
		self.assertIn(f"In stock: *{sum(1000 - i for i in range(25)):,}* Nos", reply)

	def test_non_stock_item(self):
		service = frappe._dict(ITEM, is_stock_item=0)
		with self.fake(items=[service]):
			reply = stock.get_stock("XYZ-123", MAPPED)
		self.assertEqual(reply, stock.NOT_STOCK_ITEM_REPLY.format(code="XYZ-123"))


class TestStockRefused(StockTestCase):
	def test_unmapped_sender_is_refused_without_any_lookup(self):
		with self.fake() as fake:
			reply = stock.get_stock("XYZ-123", UNMAPPED)
		self.assertEqual(reply, stock.NOT_LINKED_REPLY)
		self.assertEqual(fake.set_user_calls, [])
		self.assertEqual(fake.queries, [])

	def test_missing_item_code_shows_usage(self):
		with self.fake() as fake:
			self.assertEqual(stock.get_stock("  ", MAPPED), stock.USAGE_REPLY)
		self.assertEqual(fake.queries, [])

	def test_permission_denied(self):
		for doctype in ("Item", "Bin"):
			with self.fake(denied_doctypes={doctype}) as fake:
				reply = stock.get_stock("XYZ-123", MAPPED)
			self.assertEqual(reply, stock.PERMISSION_DENIED_REPLY, doctype)
			self.assert_ran_as_user_and_restored(fake)

	def test_permission_error_from_query_is_a_polite_refusal(self):
		with self.fake(get_list_error=frappe.PermissionError("no")) as fake:
			reply = stock.get_stock("XYZ-123", MAPPED)
		self.assertEqual(reply, stock.PERMISSION_DENIED_REPLY)
		self.assert_ran_as_user_and_restored(fake)

	def test_unknown_item(self):
		with self.fake(items=[]) as fake:
			reply = stock.get_stock("NOPE-1", MAPPED)
		self.assertEqual(reply, stock.ITEM_NOT_FOUND_REPLY.format(code="NOPE-1"))
		self.assert_ran_as_user_and_restored(fake)

	def test_overlong_item_code_is_not_queried(self):
		with self.fake() as fake:
			reply = stock.get_stock("X" * 500, MAPPED)
		self.assertIn("not found", reply)
		self.assertLess(len(reply), 200)
		self.assertEqual(fake.queries, [])


class TestSessionRestore(StockTestCase):
	def test_session_restored_when_lookup_crashes(self):
		with self.fake(get_list_error=RuntimeError("db down")) as fake:
			with self.assertRaises(RuntimeError):
				stock.get_stock("XYZ-123", MAPPED)
		self.assert_ran_as_user_and_restored(fake)

	def test_as_user_refuses_reserved_users(self):
		for user in (None, "", "Guest", "Administrator"):
			with self.fake() as fake:
				with self.assertRaises(frappe.PermissionError):
					with user_context.as_user(user):
						pass
			self.assertEqual(fake.set_user_calls, [], user)

	def test_as_user_restores_original_user(self):
		with self.fake() as fake:
			fake.session.user = "someone@frontec.in"
			with user_context.as_user(USER):
				self.assertEqual(frappe.session.user, USER)
		self.assertEqual(fake.session.user, "someone@frontec.in")


if __name__ == "__main__":
	unittest.main()
