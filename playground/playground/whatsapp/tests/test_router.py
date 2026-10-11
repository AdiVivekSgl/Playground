# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Intent router tests.

	bench --site <site> run-tests --module playground.playground.whatsapp.tests.test_router
"""

import unittest
from unittest.mock import patch

from playground.playground.whatsapp import router, stock
from playground.playground.whatsapp.phone import Sender

SENDER = Sender(phone="919812345678", user=None)


class TestRouter(unittest.TestCase):
	def test_parse_command(self):
		self.assertEqual(router.parse_command("  Stock   XYZ-123 "), ("stock", "XYZ-123"))
		self.assertEqual(router.parse_command("Hello!"), ("hello", ""))
		self.assertEqual(router.parse_command(""), ("", ""))
		self.assertEqual(router.parse_command(None), ("", ""))

	def test_hello_and_ping(self):
		self.assertEqual(router.route("Hello", SENDER), router.RouteResult("hello", "Hello from Frontec ERP 👋"))
		self.assertEqual(router.route("ping", SENDER), router.RouteResult("ping", "pong"))

	def test_unknown(self):
		result = router.route("order SO-00045", SENDER)
		self.assertEqual(result.intent, "unknown")
		self.assertEqual(result.reply, router.UNKNOWN_REPLY)

	def test_only_registered_commands_can_run(self):
		# Text that looks like a Python/Frappe path or dunder must not resolve to anything.
		for text in ("frappe.db.sql", "__import__", "route", "COMMANDS", "get_stock XYZ"):
			self.assertEqual(router.route(text, SENDER).intent, "unknown", text)

	def test_stock_is_registered(self):
		self.assertIs(router.COMMANDS["stock"], stock.get_stock)
		with patch.object(router, "COMMANDS", {**router.COMMANDS, "stock": lambda args, sender: f"stock:{args}"}):
			self.assertEqual(router.route("Stock XYZ-123", SENDER), router.RouteResult("stock", "stock:XYZ-123"))

	def test_natural_language_stock_questions(self):
		for text in (
			"How many pcs of XYZ-123 are available?",
			"how many pieces of XYZ-123 are available",
			"How much stock of item XYZ-123 do we have?",
			"how many nos of XYZ-123 left",
			"how  many   pcs of XYZ-123?",
			"stock of XYZ-123",
			"What is the stock for XYZ-123?",
		):
			self.assertEqual(router.parse_natural_language(text), ("stock", "XYZ-123"), text)

	def test_natural_language_is_narrow(self):
		for text in (
			"how many orders are pending?",
			"How many pcs of XYZ-123 and ABC-9 are available?",
			"please tell me how many pcs of XYZ-123 are available",
			"stock XYZ-123",  # plain command - handled by parse_command
			"hello",
			"",
			None,
		):
			self.assertIsNone(router.parse_natural_language(text), text)

	def test_natural_language_routes_to_stock_command(self):
		with patch.object(router, "COMMANDS", {**router.COMMANDS, "stock": lambda args, sender: f"stock:{args}"}):
			result = router.route("How many pcs of XYZ-123 are available?", SENDER)
		self.assertEqual(result, router.RouteResult("stock", "stock:XYZ-123"))

	def test_registry_is_extensible(self):
		def order(args, sender):
			return f"order {args}"

		original = dict(router.COMMANDS)
		router.COMMANDS["order"] = order
		try:
			self.assertEqual(router.route("order SO-00045", SENDER), router.RouteResult("order", "order SO-00045"))
		finally:
			router.COMMANDS.clear()
			router.COMMANDS.update(original)


if __name__ == "__main__":
	unittest.main()
