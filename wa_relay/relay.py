# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""WA-AKG -> Frappe relay (Phase 2).

Runs next to WA-AKG in Docker on the same PC. Nothing here is exposed to the
internet: WA-AKG posts webhooks to it over the private Docker network, and it
makes only outbound calls.

	WhatsApp -> WA-AKG --webhook--> relay --HTTPS + X-WA-AKG-SECRET--> Frappe
	WhatsApp <- WA-AKG <--send API-- relay <--------- {reply} ----------'

Per incoming webhook it:
  1. verifies WA-AKG's HMAC signature (X-Webhook-Signature) - fail closed;
  2. answers WA-AKG 200 straight away and handles the message in a thread;
  3. keeps only 1:1 incoming text messages (no groups, own messages, media,
     status updates), optionally only from ALLOWED_NUMBERS;
  4. calls playground.api.whatsapp.handle_message on Frappe;
  5. sends Frappe's `reply` back to the chat via WA-AKG's send API.

Standard library only (no pip installs). Configuration comes from environment
variables - see .env.example. Secrets are never logged.
"""

import hashlib
import hmac
import json
import logging
import os
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

FRAPPE_METHOD = "/api/method/playground.api.whatsapp.handle_message"
MAX_BODY_BYTES = 256 * 1024
PERSONAL_JID_SUFFIX = "@s.whatsapp.net"

log = logging.getLogger("wa_relay")


# --- configuration ---------------------------------------------------------


@dataclass(frozen=True)
class Config:
	frappe_url: str
	frappe_secret: str
	wa_akg_url: str
	wa_akg_api_key: str
	webhook_secret: str
	allowed_numbers: frozenset = field(default_factory=frozenset)
	session_ids: frozenset = field(default_factory=frozenset)
	port: int = 8787
	timeout: float = 20.0


def _csv_set(value, digits_only=False):
	items = (item.strip() for item in (value or "").split(","))
	if digits_only:
		items = ("".join(ch for ch in item if ch.isdigit()) for item in items)
	return frozenset(item for item in items if item)


def load_config(env=None):
	env = os.environ if env is None else env
	missing = [
		name
		for name in ("FRAPPE_URL", "FRAPPE_WA_AKG_SECRET", "WA_AKG_API_KEY", "WEBHOOK_SECRET")
		if not env.get(name, "").strip()
	]
	if missing:
		raise SystemExit(f"Missing required environment variables: {', '.join(missing)}")

	frappe_url = env["FRAPPE_URL"].strip().rstrip("/")
	if not frappe_url.startswith("https://") and env.get("ALLOW_INSECURE_FRAPPE_URL") != "1":
		raise SystemExit("FRAPPE_URL must be https:// (the Frappe secret travels in a header)")

	return Config(
		frappe_url=frappe_url,
		frappe_secret=env["FRAPPE_WA_AKG_SECRET"].strip(),
		wa_akg_url=(env.get("WA_AKG_URL") or "http://wa-akg-app:3000").strip().rstrip("/"),
		wa_akg_api_key=env["WA_AKG_API_KEY"].strip(),
		webhook_secret=env["WEBHOOK_SECRET"].strip(),
		allowed_numbers=_csv_set(env.get("ALLOWED_NUMBERS"), digits_only=True),
		session_ids=_csv_set(env.get("SESSION_IDS")),
		port=int(env.get("PORT") or 8787),
		timeout=float(env.get("HTTP_TIMEOUT") or 20),
	)


# --- webhook parsing -------------------------------------------------------


def verify_signature(secret, body, header):
	"""WA-AKG signs the raw body: X-Webhook-Signature: sha256=<hex HMAC-SHA256(secret, body)>."""
	if not secret or not header:
		return False
	provided = header.strip()
	if provided.lower().startswith("sha256="):
		provided = provided[len("sha256="):]
	expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
	return hmac.compare_digest(provided.lower(), expected)


@dataclass(frozen=True)
class IncomingMessage:
	message_id: str
	session_id: str
	chat_jid: str  # where the reply goes
	phone: str  # sender's number, international digits
	text: str


def _phone_from_jid(jid):
	if not isinstance(jid, str) or not jid.endswith(PERSONAL_JID_SUFFIX):
		return None
	digits = jid[: -len(PERSONAL_JID_SUFFIX)].split(":", 1)[0]
	return digits if digits.isdigit() else None


def extract_message(payload):
	"""Return (IncomingMessage, None) for a message we should answer, else (None, reason to skip)."""
	if not isinstance(payload, dict):
		return None, "payload is not an object"
	if payload.get("event") != "message.received":
		return None, f"event {payload.get('event')!r} ignored"
	data = payload.get("data")
	if not isinstance(data, dict):
		return None, "no data"
	key = data.get("key") if isinstance(data.get("key"), dict) else {}

	if key.get("fromMe") or data.get("fromMe"):
		return None, "own message"
	if data.get("isGroup") or str(data.get("chatType", "")).upper() == "GROUP":
		return None, "group message"
	if str(data.get("type", "")).upper() != "TEXT":
		return None, f"non-text message ({data.get('type')})"
	text = data.get("content")
	if not isinstance(text, str) or not text.strip():
		return None, "empty text"

	chat_jid = data.get("from") or key.get("remoteJid")
	if not isinstance(chat_jid, str) or not chat_jid or chat_jid.endswith("@g.us") or chat_jid == "status@broadcast":
		return None, "not a personal chat"

	# Newer WhatsApp may address chats by a privacy ID ("...@lid") instead of the
	# number; take the real number from whichever field carries a phone JID, and
	# never treat a LID as a phone number.
	phone = None
	for candidate in (data.get("from"), data.get("sender"), key.get("remoteJid"), key.get("senderPn"),
					  key.get("remoteJidAlt"), data.get("senderPn")):
		phone = _phone_from_jid(candidate)
		if phone:
			break
	if not phone:
		return None, "sender has no phone-number JID (LID-only)"

	session_id = payload.get("sessionId")
	if not isinstance(session_id, str) or not session_id:
		return None, "no sessionId"

	return IncomingMessage(
		message_id=str(key.get("id") or ""),
		session_id=session_id,
		chat_jid=chat_jid,
		phone=phone,
		text=text.strip(),
	), None


class RecentIds:
	"""Remembers recent WhatsApp message ids so a retried webhook isn't answered twice."""

	def __init__(self, size=2000):
		self._ids = OrderedDict()
		self._size = size
		self._lock = threading.Lock()

	def seen(self, message_id):
		if not message_id:
			return False
		with self._lock:
			if message_id in self._ids:
				return True
			self._ids[message_id] = True
			if len(self._ids) > self._size:
				self._ids.popitem(last=False)
			return False


def mask_phone(phone):
	return phone if len(phone) <= 4 else "*" * (len(phone) - 4) + phone[-4:]


# --- outbound calls --------------------------------------------------------


def _post_json(url, body, headers, timeout):
	"""POST JSON; return (http_status, parsed_json_or_None). Network errors -> (None, None)."""
	request = urllib.request.Request(
		url,
		data=json.dumps(body).encode("utf-8"),
		headers={"Content-Type": "application/json; charset=utf-8", "Accept": "application/json", **headers},
		method="POST",
	)
	try:
		with urllib.request.urlopen(request, timeout=timeout) as response:
			status, raw = response.status, response.read()
	except urllib.error.HTTPError as exc:
		status, raw = exc.code, exc.read()
	except (urllib.error.URLError, TimeoutError, OSError) as exc:
		log.warning("POST %s failed: %s", url, exc.__class__.__name__)
		return None, None
	try:
		return status, json.loads(raw.decode("utf-8"))
	except (ValueError, UnicodeDecodeError):
		return status, None


def call_frappe(config, message):
	return _post_json(
		config.frappe_url + FRAPPE_METHOD,
		{"phone": message.phone, "message": message.text, "session_id": message.session_id},
		{"X-WA-AKG-SECRET": config.frappe_secret},
		config.timeout,
	)


def send_whatsapp(config, session_id, jid, text):
	url = "{}/api/messages/{}/{}/send".format(
		config.wa_akg_url, urllib.parse.quote(session_id, safe=""), urllib.parse.quote(jid, safe="@.")
	)
	return _post_json(url, {"message": {"text": text}}, {"X-API-Key": config.wa_akg_api_key}, config.timeout)


# --- processing ------------------------------------------------------------


def process_payload(config, payload, recent):
	"""Handle one verified webhook payload. Returns a short outcome string (for logs/tests)."""
	message, skip_reason = extract_message(payload)
	if message is None:
		log.debug("skipped: %s", skip_reason)
		return f"skipped: {skip_reason}"
	who = mask_phone(message.phone)
	if config.session_ids and message.session_id not in config.session_ids:
		return "skipped: session not enabled"
	if config.allowed_numbers and message.phone not in config.allowed_numbers:
		log.info("ignored message from %s (not in ALLOWED_NUMBERS)", who)
		return "skipped: sender not allowed"
	if recent.seen(message.message_id):
		return "skipped: duplicate"

	status, body = call_frappe(config, message)
	if status != 200 or not isinstance(body, dict) or body.get("success") is not True:
		error = body.get("error") if isinstance(body, dict) else None
		log.warning("Frappe rejected message from %s: HTTP %s %s", who, status, error or "")
		return f"frappe error: {status}"
	reply = body.get("reply")
	if not isinstance(reply, str) or not reply:
		log.warning("Frappe returned no reply for %s", who)
		return "frappe error: empty reply"

	send_status, send_body = send_whatsapp(config, message.session_id, message.chat_jid, reply)
	if send_status != 200:
		detail = send_body.get("message") if isinstance(send_body, dict) else None
		log.warning("WA-AKG send to %s failed: HTTP %s %s", who, send_status, detail or "")
		return f"send error: {send_status}"
	log.info("replied to %s (%d chars)", who, len(reply))
	return "replied"


# --- HTTP server -----------------------------------------------------------


def make_handler(config, recent, run_async=True):
	class WebhookHandler(BaseHTTPRequestHandler):
		server_version = "wa-relay"

		def log_message(self, fmt, *args):  # keep the default access log quiet
			log.debug("%s %s", self.address_string(), fmt % args)

		def _reply(self, status, body):
			data = json.dumps(body).encode()
			self.send_response(status)
			self.send_header("Content-Type", "application/json")
			self.send_header("Content-Length", str(len(data)))
			self.end_headers()
			self.wfile.write(data)

		def do_GET(self):
			if self.path == "/health":
				return self._reply(200, {"ok": True})
			return self._reply(404, {"ok": False})

		def do_POST(self):
			if self.path != "/webhook":
				return self._reply(404, {"ok": False})
			try:
				length = int(self.headers.get("Content-Length") or 0)
			except ValueError:
				length = -1
			if length < 0 or length > MAX_BODY_BYTES:
				return self._reply(413, {"ok": False, "error": "body too large"})
			body = self.rfile.read(length)

			if not verify_signature(config.webhook_secret, body, self.headers.get("X-Webhook-Signature")):
				log.warning("rejected webhook with missing/invalid signature from %s", self.address_string())
				return self._reply(401, {"ok": False, "error": "invalid signature"})
			try:
				payload = json.loads(body.decode("utf-8"))
			except (ValueError, UnicodeDecodeError):
				return self._reply(400, {"ok": False, "error": "invalid JSON"})

			# Acknowledge first so WA-AKG never waits on Frappe.
			self._reply(200, {"ok": True})
			if run_async:
				threading.Thread(target=_safe_process, args=(config, payload, recent), daemon=True).start()
			else:
				_safe_process(config, payload, recent)

	return WebhookHandler


def _safe_process(config, payload, recent):
	try:
		process_payload(config, payload, recent)
	except Exception:
		log.exception("unexpected error handling webhook")


def main():
	logging.basicConfig(
		level=os.environ.get("LOG_LEVEL", "INFO").upper(),
		format="%(asctime)s %(levelname)s %(message)s",
	)
	config = load_config()
	server = ThreadingHTTPServer(("0.0.0.0", config.port), make_handler(config, RecentIds()))
	log.info(
		"wa-relay listening on :%s -> %s (allowlist: %s)",
		config.port,
		config.frappe_url,
		f"{len(config.allowed_numbers)} number(s)" if config.allowed_numbers else "off",
	)
	server.serve_forever()


if __name__ == "__main__":
	main()
