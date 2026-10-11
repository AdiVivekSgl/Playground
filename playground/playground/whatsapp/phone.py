# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Phone normalisation and WhatsApp number -> ERPNext User mapping.

WA-AKG (and WhatsApp generally) is not consistent about number format: we may
see "+91 98123 45678", "919812345678", "00919812345678", "09812345678",
"9812345678" or a JID such as "919812345678@s.whatsapp.net". Everything is
reduced to E.164 digits without the "+" ("919812345678") before matching.
"""

import re
from dataclasses import dataclass

import frappe

# Country code assumed for bare national numbers (10 digits, or 0 + 10 digits).
# Override per site with `wa_akg_default_country_code` in site config.
DEFAULT_COUNTRY_CODE = "91"
NATIONAL_NUMBER_LENGTH = 10
# E.164 allows at most 15 digits; anything under 8 is not a real subscriber number.
MIN_DIGITS = 8
MAX_DIGITS = 15

# Admin-maintained number -> User mapping (System Manager only). See README.md.
MAPPING_DOCTYPE = "WhatsApp User"
# Never reachable from WhatsApp: Guest is "nobody", Administrator bypasses all permissions.
RESERVED_USERS = frozenset({"Guest", "Administrator"})


@dataclass(frozen=True)
class Sender:
	"""Who sent a WhatsApp message, as resolved server-side.

	`user` is None until the number is mapped to an ERPNext User. Handlers that
	touch business data must refuse when it is None and must run their queries
	with that user's permissions - never with the Guest/Administrator context
	the endpoint itself runs in.
	"""

	phone: str
	user: str | None = None


def _default_country_code():
	code = str(frappe.conf.get("wa_akg_default_country_code") or DEFAULT_COUNTRY_CODE)
	return re.sub(r"\D", "", code) or DEFAULT_COUNTRY_CODE


def normalize_phone(phone):
	"""Return the number as E.164 digits without "+" (e.g. "919812345678"), or None if invalid."""
	if phone is None:
		return None
	text = str(phone).strip()
	# WhatsApp JIDs: "919812345678@s.whatsapp.net", "919812345678:12@c.us" (device suffix).
	text = text.split("@", 1)[0].split(":", 1)[0]
	if not text:
		return None

	digits = re.sub(r"\D", "", text)
	if text.startswith("+"):
		pass  # already international
	elif digits.startswith("00"):
		digits = digits[2:]  # international dialling prefix
	elif len(digits) == NATIONAL_NUMBER_LENGTH + 1 and digits.startswith("0"):
		digits = _default_country_code() + digits[1:]  # trunk-prefixed national number
	elif len(digits) == NATIONAL_NUMBER_LENGTH:
		digits = _default_country_code() + digits  # bare national number

	if not MIN_DIGITS <= len(digits) <= MAX_DIGITS:
		return None
	return digits


def get_user_from_phone(phone):
	"""Resolve a WhatsApp number to a Sender (normalised phone + ERPNext User).

	The user comes only from an enabled "WhatsApp User" row for the normalised
	number - nothing the sender types can choose or claim a user. Unmapped or
	disabled numbers, disabled Users and Guest/Administrator all give user=None.
	"""
	normalized = normalize_phone(phone)
	if not normalized:
		return None
	return Sender(phone=normalized, user=_mapped_user(normalized))


def _mapped_user(phone):
	# Plain db reads: this is server-side configuration looked up by the endpoint
	# (running as Guest), not data returned to the sender.
	user = frappe.db.get_value(MAPPING_DOCTYPE, {"phone": phone, "enabled": 1}, "user")
	if not user or user in RESERVED_USERS:
		return None
	if not frappe.db.get_value("User", user, "enabled"):
		return None
	return user
