# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Field-type-aware value formatting.

Turns a raw Frappe field value into the string that goes into the Google Doc,
using sensible defaults per fieldtype and honouring any explicit transformation
configured on the mapping row. Kept dependency-light and pure so it is trivial
to unit-test without a site.
"""

import frappe
from frappe.utils import flt, fmt_money, format_datetime, formatdate

# Transformation options offered on the mapping rows (Formatting / Transformation).
TRANSFORM_NONE = ""
TRANSFORM_UPPER = "Uppercase"
TRANSFORM_LOWER = "Lowercase"
TRANSFORM_TITLE = "Title Case"

TRANSFORMS = [TRANSFORM_NONE, TRANSFORM_UPPER, TRANSFORM_LOWER, TRANSFORM_TITLE]


def format_value(value, fieldtype=None, transform=None, currency=None, default=None):
	"""Format `value` for insertion into a Google Doc.

	fieldtype drives the default rendering (currency/float/date/check/…); an
	optional transform (case change) is applied on top. Empty values fall back to
	`default` (from the mapping) or an empty string — never the literal "None".
	"""
	if value in (None, ""):
		value = default if default not in (None, "") else ""
		# A blank has no type-specific formatting to apply.
		return _apply_transform(str(value), transform)

	text = _format_by_type(value, fieldtype, currency)
	return _apply_transform(text, transform)


def _format_by_type(value, fieldtype, currency):
	ft = (fieldtype or "Data").strip()

	if ft == "Currency":
		# fmt_money adds the currency symbol + thousands separators per system
		# number format; the caller passes the document/company currency.
		return fmt_money(flt(value), currency=currency)

	if ft in ("Float", "Percent"):
		return _trim(flt(value))

	if ft == "Int":
		try:
			return str(int(value))
		except (TypeError, ValueError):
			return str(value)

	if ft == "Check":
		return "Yes" if int(value or 0) else "No"

	if ft == "Date":
		return formatdate(value)

	if ft == "Datetime":
		return format_datetime(value)

	# Text Editor: strip HTML so the doc gets plain text (the template controls
	# styling; we do not push raw HTML into Google Docs in V1).
	if ft == "Text Editor":
		return frappe.utils.strip_html(str(value)).strip()

	# Data / Small Text / Text / Link / Select / everything else.
	return str(value)


def _trim(number):
	"""Render a float without a trailing '.0' but keep real decimals."""
	if number == int(number):
		return str(int(number))
	return f"{number:g}"


def _apply_transform(text, transform):
	if not transform:
		return text
	if transform == TRANSFORM_UPPER:
		return text.upper()
	if transform == TRANSFORM_LOWER:
		return text.lower()
	if transform == TRANSFORM_TITLE:
		return text.title()
	return text
