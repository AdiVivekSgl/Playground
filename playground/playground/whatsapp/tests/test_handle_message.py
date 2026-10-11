# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Endpoint tests for playground.api.whatsapp (auth, params, replies, logging).

Run on the bench:
	bench --site <site> run-tests --module playground.playground.whatsapp.tests.test_handle_message

Everything Frappe-side (site config, request header, log inserts) is mocked, so
nothing is written and no WhatsApp/WA-AKG call is ever made.
"""

import json
import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from playground.api import whatsapp
from playground.playground.whatsapp import phone as phone_mod
from playground.playground.whatsapp import router, stock

SECRET = "s3cr3t-test-value-0123456789abcdef"


class FakeLog:
	"""Stands in for the WhatsApp Query Log document; records insert + db_set values."""

	def __init__(self, fields):
		self.fields = dict(fields)
		self.inserted = False

	def insert(self, *a, **k):
		self.inserted = True
		return self

	def db_set(self, values, *a, **k):
		self.fields.update(values)


class WhatsAppEndpointTestCase(unittest.TestCase):
	@contextmanager
	def env(self, header=SECRET, conf_secret=SECRET, mapped_user=None):
		"""Patch site config, the secret header, the number->user lookup and log writes; yields the FakeLogs."""
		logs = []

		def get_doc(fields):
			log = FakeLog(fields)
			logs.append(log)
			return log

		conf = {"wa_akg_secret": conf_secret} if conf_secret is not None else {}
		with patch.object(whatsapp.frappe, "conf", conf), \
			patch.object(whatsapp, "_get_secret_header", return_value=header), \
			patch.object(phone_mod, "_mapped_user", return_value=mapped_user), \
			patch.object(whatsapp.frappe, "get_doc", side_effect=get_doc), \
			patch.object(whatsapp.frappe, "log_error", MagicMock()), \
			patch.object(whatsapp, "now_datetime", return_value="2026-10-10 12:00:00"):
			yield logs

	def call(self, **params):
		params.setdefault("phone", "919812345678")
		params.setdefault("message", "hello")
		params.setdefault("session_id", "frontec-test")
		response = whatsapp.process_message(**params)
		return response.status_code, json.loads(response.get_data(as_text=True))


class TestAuthentication(WhatsAppEndpointTestCase):
	def test_valid_secret_is_accepted(self):
		with self.env():
			status, body = self.call()
		self.assertEqual(status, 200)
		self.assertTrue(body["success"])

	def test_invalid_secret_is_rejected(self):
		with self.env(header="wrong-secret") as logs:
			status, body = self.call()
		self.assertEqual(status, 401)
		self.assertEqual(body, {"success": False, "error": "Unauthorized"})
		self.assertEqual(logs[0].fields["status"], "Unauthorized")

	def test_missing_secret_is_rejected(self):
		with self.env(header=None) as logs:
			status, body = self.call()
		self.assertEqual(status, 401)
		self.assertEqual(body, {"success": False, "error": "Unauthorized"})
		self.assertEqual(logs[0].fields["status"], "Unauthorized")

	def test_empty_secret_header_is_rejected(self):
		with self.env(header=""):
			status, _ = self.call()
		self.assertEqual(status, 401)

	def test_unconfigured_site_secret_rejects_everything(self):
		# Fail closed: no wa_akg_secret in site config -> nobody gets in, even with no header.
		for header in (None, "", SECRET):
			with self.env(header=header, conf_secret=None) as logs:
				status, _ = self.call()
			self.assertEqual(status, 401)
			self.assertIn("not set in site config", logs[0].fields["error_message"])

	def test_unauthorized_request_never_reaches_router(self):
		with self.env(header="wrong"), patch.object(whatsapp.router, "route") as route:
			self.call()
		route.assert_not_called()

	def test_secret_is_never_logged(self):
		for header in (SECRET, "wrong-" + SECRET):
			with self.env(header=header) as logs:
				self.call()
			for log in logs:
				for value in log.fields.values():
					self.assertNotIn(SECRET, str(value))


class TestCommands(WhatsAppEndpointTestCase):
	def test_hello(self):
		for text in ("hello", "Hello", "HELLO", "  hello  ", "Hello!"):
			with self.env():
				status, body = self.call(message=text)
			self.assertEqual(status, 200, text)
			self.assertEqual(body, {"success": True, "reply": "Hello from Frontec ERP 👋"})

	def test_ping(self):
		with self.env():
			status, body = self.call(message="ping")
		self.assertEqual(status, 200)
		self.assertEqual(body, {"success": True, "reply": "pong"})

	def test_unknown_command(self):
		for text in ("order SO-00045", "what is my outstanding", "frappe.db.sql select 1"):
			with self.env():
				status, body = self.call(message=text)
			self.assertEqual(status, 200)
			self.assertEqual(body, {"success": True, "reply": router.UNKNOWN_REPLY})

	def test_reply_is_utf8_json_not_ascii_escaped(self):
		with self.env():
			response = whatsapp.process_message(phone="919812345678", message="hello")
		self.assertIn("👋", response.get_data(as_text=True))
		self.assertTrue(response.content_type.startswith("application/json"))

	def test_handler_crash_returns_generic_error_and_logs(self):
		with self.env() as logs, patch.object(whatsapp.router, "route", side_effect=RuntimeError("db down")):
			status, body = self.call()
			# Crash detail goes to the Error Log only, never back to the caller.
			whatsapp.frappe.log_error.assert_called_once()
		self.assertEqual(status, 500)
		self.assertEqual(body, {"success": False, "error": "Internal error"})
		self.assertNotIn("db down", json.dumps(body))
		self.assertEqual(logs[0].fields["status"], "Error")


class TestParameters(WhatsAppEndpointTestCase):
	def assert_bad_request(self, expected_error, **params):
		with self.env() as logs:
			status, body = self.call(**params)
		self.assertEqual(status, 400)
		self.assertFalse(body["success"])
		self.assertIn(expected_error, body["error"])
		self.assertEqual(logs[0].fields["status"], "Error")

	def test_missing_phone(self):
		self.assert_bad_request("phone", phone=None)
		self.assert_bad_request("phone", phone="   ")

	def test_missing_message(self):
		self.assert_bad_request("message", message=None)
		self.assert_bad_request("message", message="")

	def test_wrong_types(self):
		# What a malformed-but-parseable JSON body turns into: objects/lists/bools in place of strings.
		self.assert_bad_request("phone", phone={"number": "919812345678"})
		self.assert_bad_request("phone", phone=True)
		self.assert_bad_request("message", message=["hello"])
		self.assert_bad_request("session_id", session_id={"x": 1})

	def test_message_too_long(self):
		self.assert_bad_request("longer than", message="x" * (whatsapp.MAX_MESSAGE_LENGTH + 1))

	def test_invalid_phone(self):
		self.assert_bad_request("Invalid phone number", phone="12345")
		self.assert_bad_request("Invalid phone number", phone="not-a-number")

	def test_session_id_is_optional(self):
		with self.env():
			status, _ = self.call(session_id=None)
		self.assertEqual(status, 200)

	def test_numeric_phone_is_accepted(self):
		with self.env():
			status, _ = self.call(phone=919812345678)
		self.assertEqual(status, 200)


class TestLogging(WhatsAppEndpointTestCase):
	def test_processed_request_is_logged(self):
		with self.env() as logs:
			self.call(phone="+91 98123-45678", message="Hello", session_id="frontec-test")
		self.assertEqual(len(logs), 1)
		log = logs[0]
		self.assertTrue(log.inserted)
		self.assertEqual(log.fields["doctype"], "WhatsApp Query Log")
		self.assertEqual(log.fields["phone"], "919812345678")  # normalised
		self.assertIsNone(log.fields["erpnext_user"])  # unmapped number
		self.assertEqual(log.fields["session_id"], "frontec-test")
		self.assertEqual(log.fields["incoming_message"], "Hello")
		self.assertEqual(log.fields["intent"], "hello")
		self.assertEqual(log.fields["response"], "Hello from Frontec ERP 👋")
		self.assertEqual(log.fields["status"], "Processed")
		self.assertEqual(log.fields["timestamp"], "2026-10-10 12:00:00")

	def test_mapped_user_is_logged(self):
		with self.env(mapped_user="ravi@frontec.in") as logs:
			self.call(message="hello")
		self.assertEqual(logs[0].fields["erpnext_user"], "ravi@frontec.in")
		self.assertEqual(logs[0].fields["status"], "Processed")

	def test_mapping_is_looked_up_with_normalised_number(self):
		with self.env(), patch.object(phone_mod, "_mapped_user", return_value=None) as lookup:
			self.call(phone="+91 98123-45678")
		lookup.assert_called_once_with("919812345678")

	def test_stock_from_unmapped_number_is_refused_and_logged(self):
		with self.env() as logs:
			status, body = self.call(message="stock XYZ-123")
		self.assertEqual(status, 200)
		self.assertEqual(body["reply"], stock.NOT_LINKED_REPLY)
		self.assertEqual(logs[0].fields["intent"], "stock")
		self.assertIsNone(logs[0].fields["erpnext_user"])

	def test_unknown_command_logged_with_unknown_intent(self):
		with self.env() as logs:
			self.call(message="order SO-00045")
		self.assertEqual(logs[0].fields["intent"], "unknown")
		self.assertEqual(logs[0].fields["status"], "Processed")

	def test_long_message_is_clipped_in_log(self):
		with self.env() as logs:
			self.call(message="hello " + "x" * 3000)
		self.assertLessEqual(len(logs[0].fields["incoming_message"]), whatsapp.LOG_TEXT_LIMIT)

	def test_log_failure_does_not_block_reply(self):
		with self.env(), patch.object(whatsapp.frappe, "get_doc", side_effect=Exception("log table missing")):
			status, body = self.call()
		self.assertEqual(status, 200)
		self.assertEqual(body["reply"], "Hello from Frontec ERP 👋")


if __name__ == "__main__":
	unittest.main()
