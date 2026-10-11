# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Phone normalisation + sender mapping tests.

	bench --site <site> run-tests --module playground.playground.whatsapp.tests.test_phone
"""

import unittest
from unittest.mock import patch

from playground.playground.whatsapp import phone as phone_mod
from playground.playground.whatsapp.phone import Sender, get_user_from_phone, normalize_phone


class TestNormalizePhone(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(phone_mod.frappe, "conf", {})
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_equivalent_formats_normalise_to_the_same_number(self):
		expected = "919812345678"
		for raw in (
			"919812345678",
			"+919812345678",
			"+91 98123 45678",
			"+91-98123-45678",
			"(+91) 98123 45678",
			"00919812345678",
			"09812345678",
			"9812345678",
			"919812345678@s.whatsapp.net",
			"919812345678@c.us",
			"919812345678:12@s.whatsapp.net",
			919812345678,
		):
			self.assertEqual(normalize_phone(raw), expected, raw)

	def test_foreign_numbers_keep_their_country_code(self):
		self.assertEqual(normalize_phone("+44 7700 900123"), "447700900123")
		self.assertEqual(normalize_phone("0044 7700 900123"), "447700900123")
		self.assertEqual(normalize_phone("+1 (415) 555-0100"), "14155550100")

	def test_default_country_code_is_configurable(self):
		with patch.object(phone_mod.frappe, "conf", {"wa_akg_default_country_code": "+1"}):
			self.assertEqual(normalize_phone("415 555 0100"), "14155550100")
			self.assertEqual(normalize_phone("+919812345678"), "919812345678")

	def test_invalid_numbers(self):
		for raw in (None, "", "   ", "abc", "12345", "@s.whatsapp.net", "+1234567890123456"):
			self.assertIsNone(normalize_phone(raw), raw)


class FakeDB:
	"""Answers the two get_value lookups _mapped_user makes, from in-memory rows."""

	def __init__(self, mappings=(), users=None):
		self.mappings = list(mappings)  # (phone, user, enabled)
		self.users = users or {}  # user -> enabled
		self.calls = []

	def get_value(self, doctype, filters, fieldname=None, *a, **k):
		self.calls.append((doctype, filters, fieldname))
		if doctype == "WhatsApp User":
			for phone, user, enabled in self.mappings:
				if phone == filters["phone"] and enabled == filters["enabled"]:
					return user
			return None
		if doctype == "User":
			return self.users.get(filters)
		raise AssertionError(f"unexpected lookup on {doctype}")


class TestGetUserFromPhone(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(phone_mod.frappe, "conf", {})
		patcher.start()
		self.addCleanup(patcher.stop)

	def resolve(self, raw, mappings=(), users=None):
		db = FakeDB(mappings, users)
		with patch.object(phone_mod.frappe, "db", db):
			return get_user_from_phone(raw), db

	def test_mapped_number_resolves_from_any_format(self):
		mappings = [("919812345678", "ravi@frontec.in", 1)]
		users = {"ravi@frontec.in": 1}
		for raw in ("+91 98123 45678", "09812345678", "9812345678", "919812345678@s.whatsapp.net"):
			sender, db = self.resolve(raw, mappings, users)
			self.assertEqual(sender, Sender(phone="919812345678", user="ravi@frontec.in"), raw)
			# The lookup always uses the normalised number, never the raw text.
			self.assertEqual(db.calls[0], ("WhatsApp User", {"phone": "919812345678", "enabled": 1}, "user"))

	def test_unmapped_number_has_no_user(self):
		sender, _ = self.resolve("+91 98123 45678", [("919800000000", "ravi@frontec.in", 1)], {"ravi@frontec.in": 1})
		self.assertEqual(sender, Sender(phone="919812345678", user=None))

	def test_disabled_mapping_has_no_user(self):
		sender, _ = self.resolve("919812345678", [("919812345678", "ravi@frontec.in", 0)], {"ravi@frontec.in": 1})
		self.assertIsNone(sender.user)

	def test_disabled_erpnext_user_has_no_user(self):
		sender, _ = self.resolve("919812345678", [("919812345678", "ravi@frontec.in", 1)], {"ravi@frontec.in": 0})
		self.assertIsNone(sender.user)

	def test_deleted_erpnext_user_has_no_user(self):
		sender, _ = self.resolve("919812345678", [("919812345678", "gone@frontec.in", 1)], {})
		self.assertIsNone(sender.user)

	def test_reserved_users_are_never_returned(self):
		for user in ("Administrator", "Guest"):
			sender, _ = self.resolve("919812345678", [("919812345678", user, 1)], {user: 1})
			self.assertIsNone(sender.user, user)

	def test_invalid_phone_returns_none_without_lookup(self):
		sender, db = self.resolve("12")
		self.assertIsNone(sender)
		self.assertEqual(db.calls, [])


if __name__ == "__main__":
	unittest.main()
