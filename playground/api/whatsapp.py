# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""WA-AKG -> Frappe endpoint: POST /api/method/playground.api.whatsapp.handle_message

The only thing WA-AKG can call. It authenticates with a shared secret in the
X-WA-AKG-SECRET header (compared against `wa_akg_secret` in site config), maps
the sender's number server-side, routes the text to a fixed set of handlers
(playground/playground/whatsapp/router.py) and logs every request to
"WhatsApp Query Log". It never runs a method, DocType or query named by the
caller. See playground/playground/whatsapp/README.md.
"""

import hmac
import json

import frappe
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime
from werkzeug.wrappers import Response

from playground.playground.whatsapp import router
from playground.playground.whatsapp.phone import get_user_from_phone

SECRET_HEADER = "X-WA-AKG-SECRET"
SECRET_CONF_KEY = "wa_akg_secret"
LOG_DOCTYPE = "WhatsApp Query Log"

MAX_MESSAGE_LENGTH = 4096
# Clip what goes into the log so a flood of junk can't bloat the table.
LOG_TEXT_LIMIT = 1000
LOG_DATA_LIMIT = 140

ERR_UNAUTHORIZED = "Unauthorized"
ERR_INTERNAL = "Internal error"


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=120, seconds=60)
def handle_message(phone=None, message=None, session_id=None):
	# allow_guest: WA-AKG has no Frappe session - the shared secret is the
	# authentication, checked first thing in process_message().
	return process_message(phone=phone, message=message, session_id=session_id)


def process_message(phone=None, message=None, session_id=None):
	log_fields = {
		"phone": _clip(phone, LOG_DATA_LIMIT),
		"session_id": _clip(session_id, LOG_DATA_LIMIT),
		"incoming_message": _clip(message, LOG_TEXT_LIMIT),
		"remote_ip": _clip(getattr(frappe.local, "request_ip", None), LOG_DATA_LIMIT),
	}

	auth_error = _check_secret()
	if auth_error:
		_create_log(status="Unauthorized", error_message=auth_error, **log_fields)
		return _respond(401, error=ERR_UNAUTHORIZED)

	param_error = _validate_params(phone, message, session_id)
	if param_error:
		_create_log(status="Error", error_message=param_error, **log_fields)
		return _respond(400, error=param_error)

	sender = get_user_from_phone(phone)
	if sender is None:
		_create_log(status="Error", error_message="Invalid phone number", **log_fields)
		return _respond(400, error="Invalid phone number")

	log_fields["phone"] = sender.phone
	log_fields["erpnext_user"] = sender.user
	log = _create_log(status="Received", **log_fields)

	try:
		result = router.route(message, sender)
	except Exception:
		frappe.log_error(title="WhatsApp message handling failed")
		_update_log(log, status="Error", error_message="Unhandled exception - see Error Log")
		return _respond(500, error=ERR_INTERNAL)

	_update_log(log, status="Processed", intent=result.intent, response=_clip(result.reply, LOG_TEXT_LIMIT))
	return _respond(200, reply=result.reply)


def _check_secret():
	"""Return None when the request carries the right secret, else a reason for the log.

	The reason is only ever written to the log (readable by System Manager);
	the caller always gets the same "Unauthorized" so it can't tell which case hit.
	"""
	expected = frappe.conf.get(SECRET_CONF_KEY)
	if not expected:
		return f"{SECRET_CONF_KEY} is not set in site config - all requests are rejected"
	provided = _get_secret_header()
	if not provided:
		return f"Missing {SECRET_HEADER} header"
	if not hmac.compare_digest(str(provided).encode(), str(expected).encode()):
		return f"Invalid {SECRET_HEADER} header"
	return None


def _get_secret_header():
	return frappe.get_request_header(SECRET_HEADER)


def _validate_params(phone, message, session_id):
	if phone is None or (isinstance(phone, str) and not phone.strip()):
		return "Missing required parameter: phone"
	if not isinstance(phone, (str, int)) or isinstance(phone, bool):
		return "Invalid parameter: phone must be a string"
	if message is None or (isinstance(message, str) and not message.strip()):
		return "Missing required parameter: message"
	if not isinstance(message, str):
		return "Invalid parameter: message must be a string"
	if len(message) > MAX_MESSAGE_LENGTH:
		return f"Invalid parameter: message longer than {MAX_MESSAGE_LENGTH} characters"
	if session_id is not None and not isinstance(session_id, (str, int)):
		return "Invalid parameter: session_id must be a string"
	return None


def _respond(status_code, reply=None, error=None):
	if error is None:
		body = {"success": True, "reply": reply}
	else:
		body = {"success": False, "error": error}
	return Response(
		json.dumps(body, ensure_ascii=False),
		status=status_code,
		content_type="application/json; charset=utf-8",
	)


def _create_log(**fields):
	"""Insert a WhatsApp Query Log row. A logging failure never blocks the reply."""
	try:
		log = frappe.get_doc({"doctype": LOG_DOCTYPE, "timestamp": now_datetime(), **fields})
		log.insert(ignore_permissions=True)
		return log
	except Exception:
		frappe.log_error(title="WhatsApp Query Log write failed")
		return None


def _update_log(log, **fields):
	if log is None:
		return
	try:
		log.db_set(fields)
	except Exception:
		frappe.log_error(title="WhatsApp Query Log update failed")


def _clip(value, limit):
	if value is None:
		return None
	text = str(value)
	return text if len(text) <= limit else text[: limit - 1] + "…"
