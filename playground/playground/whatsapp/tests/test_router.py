# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Intent router tests.

	bench --site <site> run-tests --module playground.playground.whatsapp.tests.test_router
"""

import unittest

from playground.playground.whatsapp import router
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

	def test_registry_is_extensible(self):
		def stock(args, sender):
			return f"stock for {args}"

		original = dict(router.COMMANDS)
		router.COMMANDS["stock"] = stock
		try:
			self.assertEqual(router.route("stock XYZ-123", SENDER), router.RouteResult("stock", "stock for XYZ-123"))
		finally:
			router.COMMANDS.clear()
			router.COMMANDS.update(original)


if __name__ == "__main__":
	unittest.main()
