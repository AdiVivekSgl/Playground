# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Relay tests - stdlib only, no network. Run: python -m unittest test_relay"""

import hashlib
import hmac
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import relay

CONFIG = relay.Config(
	frappe_url="https://frontec.example.frappe.cloud",
	frappe_secret="frappe-secret-value",
	wa_akg_url="http://wa-akg-app:3000",
	wa_akg_api_key="wa-api-key-value",
	webhook_secret="webhook-secret-value",
)


def dm(text="hello", **overrides):
	data = {
		"key": {"id": "AB12CD34EF", "remoteJid": "919812345678@s.whatsapp.net", "fromMe": False},
		"pushName": "Test",
		"from": "919812345678@s.whatsapp.net",
		"sender": "919812345678@s.whatsapp.net",
		"isGroup": False,
		"chatType": "PERSONAL",
		"type": "TEXT",
		"content": text,
	}
	data.update(overrides)
	return {"event": "message.received", "sessionId": "frontec-test", "timestamp": "2026-10-10T12:00:00Z", "data": data}


def sign(body, secret=CONFIG.webhook_secret):
	return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestSignature(unittest.TestCase):
	def test_valid(self):
		body = b'{"event":"message.received"}'
		self.assertTrue(relay.verify_signature("s", body, sign(body, "s")))
		self.assertTrue(relay.verify_signature("s", body, sign(body, "s")[len("sha256="):]))  # bare hex

	def test_invalid_missing_or_unconfigured(self):
		body = b"{}"
		self.assertFalse(relay.verify_signature("s", body, sign(body, "other")))
		self.assertFalse(relay.verify_signature("s", body, None))
		self.assertFalse(relay.verify_signature("s", body, ""))
		self.assertFalse(relay.verify_signature("", body, sign(body, "")))  # no secret configured -> reject
		self.assertFalse(relay.verify_signature("s", body + b" ", sign(body, "s")))  # tampered body


class TestExtractMessage(unittest.TestCase):
	def test_personal_text(self):
		message, reason = relay.extract_message(dm("Hello"))
		self.assertIsNone(reason)
		self.assertEqual(message, relay.IncomingMessage(
			message_id="AB12CD34EF", session_id="frontec-test",
			chat_jid="919812345678@s.whatsapp.net", phone="919812345678", text="Hello",
		))

	def test_skips(self):
		cases = {
			"other event": {**dm(), "event": "message.sent"},
			"own message": dm(key={"id": "x", "remoteJid": "919812345678@s.whatsapp.net", "fromMe": True}),
			"group": dm(**{"from": "1203630@g.us", "isGroup": True, "chatType": "GROUP"}),
			"group by jid": dm(**{"from": "1203630@g.us", "isGroup": False}),
			"image": dm(type="IMAGE"),
			"empty": dm("   "),
			"status": dm(**{"from": "status@broadcast"}),
			"no session": {**dm(), "sessionId": None},
			"not object": ["message.received"],
		}
		for name, payload in cases.items():
			message, reason = relay.extract_message(payload)
			self.assertIsNone(message, name)
			self.assertTrue(reason, name)

	def test_lid_chat_uses_phone_from_alt_field(self):
		payload = dm(**{
			"from": "123456789012345@lid",
			"sender": "123456789012345@lid",
			"key": {"id": "L1", "remoteJid": "123456789012345@lid", "fromMe": False,
					"senderPn": "919812345678@s.whatsapp.net"},
		})
		message, _ = relay.extract_message(payload)
		self.assertEqual(message.phone, "919812345678")
		self.assertEqual(message.chat_jid, "123456789012345@lid")  # reply goes to the same chat

	def test_lid_only_sender_is_skipped_not_treated_as_phone(self):
		payload = dm(**{
			"from": "123456789012345@lid", "sender": "123456789012345@lid",
			"key": {"id": "L2", "remoteJid": "123456789012345@lid", "fromMe": False},
		})
		message, reason = relay.extract_message(payload)
		self.assertIsNone(message)
		self.assertIn("LID", reason)

	def test_device_suffix_stripped(self):
		message, _ = relay.extract_message(dm(**{"from": "919812345678:3@s.whatsapp.net"}))
		self.assertEqual(message.phone, "919812345678")


class TestProcessPayload(unittest.TestCase):
	def run_process(self, payload, config=CONFIG, frappe=(200, {"success": True, "reply": "Hello from Frontec ERP 👋"}),
					send=(200, {"status": True}), recent=None):
		with patch.object(relay, "call_frappe", return_value=frappe) as call_frappe, \
			patch.object(relay, "send_whatsapp", return_value=send) as send_whatsapp:
			outcome = relay.process_payload(config, payload, recent or relay.RecentIds())
		return outcome, call_frappe, send_whatsapp

	def test_hello_round_trip(self):
		outcome, call_frappe, send_whatsapp = self.run_process(dm("hello"))
		self.assertEqual(outcome, "replied")
		sent_message = call_frappe.call_args.args[1]
		self.assertEqual((sent_message.phone, sent_message.text, sent_message.session_id),
						 ("919812345678", "hello", "frontec-test"))
		send_whatsapp.assert_called_once_with(CONFIG, "frontec-test", "919812345678@s.whatsapp.net",
											  "Hello from Frontec ERP 👋")

	def test_frappe_rejection_sends_nothing(self):
		for frappe in ((401, {"success": False, "error": "Unauthorized"}), (500, None), (None, None),
					   (200, {"success": False, "error": "x"}), (200, {"success": True, "reply": ""})):
			outcome, _, send_whatsapp = self.run_process(dm(), frappe=frappe)
			self.assertTrue(outcome.startswith("frappe error"), frappe)
			send_whatsapp.assert_not_called()

	def test_skipped_message_never_reaches_frappe(self):
		outcome, call_frappe, _ = self.run_process(dm(type="IMAGE"))
		self.assertTrue(outcome.startswith("skipped"))
		call_frappe.assert_not_called()

	def test_allowlist(self):
		config = relay.Config(**{**CONFIG.__dict__, "allowed_numbers": frozenset({"919800000000"})})
		outcome, call_frappe, _ = self.run_process(dm(), config=config)
		self.assertEqual(outcome, "skipped: sender not allowed")
		call_frappe.assert_not_called()
		config = relay.Config(**{**CONFIG.__dict__, "allowed_numbers": frozenset({"919812345678"})})
		self.assertEqual(self.run_process(dm(), config=config)[0], "replied")

	def test_session_filter(self):
		config = relay.Config(**{**CONFIG.__dict__, "session_ids": frozenset({"other"})})
		self.assertEqual(self.run_process(dm(), config=config)[0], "skipped: session not enabled")

	def test_duplicate_webhook_answered_once(self):
		recent = relay.RecentIds()
		self.assertEqual(self.run_process(dm(), recent=recent)[0], "replied")
		outcome, call_frappe, _ = self.run_process(dm(), recent=recent)
		self.assertEqual(outcome, "skipped: duplicate")
		call_frappe.assert_not_called()

	def test_send_failure_reported(self):
		self.assertEqual(self.run_process(dm(), send=(503, {"message": "Session not connected"}))[0], "send error: 503")


class TestOutboundRequests(unittest.TestCase):
	def test_frappe_and_wa_akg_requests(self):
		captured = []

		def fake_post(url, body, headers, timeout):
			captured.append((url, body, headers))
			return 200, {}

		message, _ = relay.extract_message(dm("ping"))
		with patch.object(relay, "_post_json", side_effect=fake_post):
			relay.call_frappe(CONFIG, message)
			relay.send_whatsapp(CONFIG, "frontec test", "919812345678@s.whatsapp.net", "pong")

		url, body, headers = captured[0]
		self.assertEqual(url, "https://frontec.example.frappe.cloud/api/method/playground.api.whatsapp.handle_message")
		self.assertEqual(body, {"phone": "919812345678", "message": "ping", "session_id": "frontec-test"})
		self.assertEqual(headers, {"X-WA-AKG-SECRET": "frappe-secret-value"})

		url, body, headers = captured[1]
		self.assertEqual(url, "http://wa-akg-app:3000/api/messages/frontec%20test/919812345678@s.whatsapp.net/send")
		self.assertEqual(body, {"message": {"text": "pong"}})
		self.assertEqual(headers, {"X-API-Key": "wa-api-key-value"})


class TestConfig(unittest.TestCase):
	BASE = {"FRAPPE_URL": "https://x.frappe.cloud/", "FRAPPE_WA_AKG_SECRET": "a", "WA_AKG_API_KEY": "b",
			"WEBHOOK_SECRET": "c", "ALLOWED_NUMBERS": "+91 98123 45678, 919800000000,"}

	def test_load(self):
		config = relay.load_config(self.BASE)
		self.assertEqual(config.frappe_url, "https://x.frappe.cloud")
		self.assertEqual(config.wa_akg_url, "http://wa-akg-app:3000")
		self.assertEqual(config.allowed_numbers, frozenset({"919812345678", "919800000000"}))

	def test_required_and_https(self):
		with self.assertRaises(SystemExit):
			relay.load_config({**self.BASE, "WEBHOOK_SECRET": ""})
		with self.assertRaises(SystemExit):
			relay.load_config({**self.BASE, "FRAPPE_URL": "http://x.frappe.cloud"})


class TestHttpServer(unittest.TestCase):
	def setUp(self):
		self.processed = []
		patcher = patch.object(relay, "process_payload", side_effect=lambda c, p, r: self.processed.append(p))
		patcher.start()
		self.addCleanup(patcher.stop)
		self.server = ThreadingHTTPServer(("127.0.0.1", 0), relay.make_handler(CONFIG, relay.RecentIds(), run_async=False))
		threading.Thread(target=self.server.serve_forever, daemon=True).start()
		self.addCleanup(self.server.server_close)
		self.addCleanup(self.server.shutdown)
		self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

	def post(self, body, signature):
		headers = {"Content-Type": "application/json"}
		if signature:
			headers["X-Webhook-Signature"] = signature
		request = urllib.request.Request(self.base + "/webhook", data=body, headers=headers, method="POST")
		try:
			with urllib.request.urlopen(request, timeout=5) as response:
				return response.status
		except urllib.error.HTTPError as exc:
			return exc.code

	def test_signed_webhook_accepted_and_processed(self):
		body = json.dumps(dm()).encode()
		self.assertEqual(self.post(body, sign(body)), 200)
		self.assertEqual(self.processed, [dm()])

	def test_unsigned_or_badly_signed_webhook_rejected(self):
		body = json.dumps(dm()).encode()
		self.assertEqual(self.post(body, None), 401)
		self.assertEqual(self.post(body, sign(body, "wrong")), 401)
		self.assertEqual(self.processed, [])

	def test_signed_but_malformed_json(self):
		body = b"{not json"
		self.assertEqual(self.post(body, sign(body)), 400)

	def test_health(self):
		with urllib.request.urlopen(self.base + "/health", timeout=5) as response:
			self.assertEqual(response.status, 200)


if __name__ == "__main__":
	unittest.main()
