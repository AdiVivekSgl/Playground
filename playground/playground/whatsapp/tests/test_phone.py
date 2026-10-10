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


class TestGetUserFromPhone(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(phone_mod.frappe, "conf", {})
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_returns_normalised_sender_with_no_user_in_phase_1(self):
		self.assertEqual(get_user_from_phone("+91 98123 45678"), Sender(phone="919812345678", user=None))

	def test_invalid_phone_returns_none(self):
		self.assertIsNone(get_user_from_phone("12"))


if __name__ == "__main__":
	unittest.main()
