# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Placeholder, managed-section and naming helpers.

* Managed named ranges — how the engine marks ERP-controlled content inside a
  generated doc so `Update` can rewrite exactly that content and leave a user's
  manual edits untouched (spec sections 10-11). A scalar field's value and a
  child table each get a named range `erpgdoc:<kind>:<key>`.
* File-name rendering from the configurable pattern, with sanitisation.
"""

import re

from playground.playground.google_docs.constants import (
	MANAGED_PREFIX,
	PLACEHOLDER_CLOSE,
	PLACEHOLDER_OPEN,
)


def field_range_name(fieldname):
	"""Named range that wraps a single ERP-controlled scalar value."""
	return f"{MANAGED_PREFIX}:field:{fieldname}"


def table_range_name(table_fieldname):
	"""Named range that wraps a single ERP-controlled child table."""
	return f"{MANAGED_PREFIX}:table:{table_fieldname}"


def normalise_placeholder(placeholder, fieldname=None):
	"""Return a `{{...}}` placeholder, defaulting to `{{fieldname}}`.

	Accepts a bare token ('supplier_name') or a full placeholder
	('{{supplier_name}}') from the mapping row and always yields the wrapped form.
	"""
	token = (placeholder or "").strip()
	if not token and fieldname:
		token = fieldname.strip()
	if not token:
		return ""
	if token.startswith(PLACEHOLDER_OPEN) and token.endswith(PLACEHOLDER_CLOSE):
		return token
	return f"{PLACEHOLDER_OPEN}{token}{PLACEHOLDER_CLOSE}"


# ---------------------------------------------------------------------------
# file naming
# ---------------------------------------------------------------------------

# Characters Drive tolerates but that make for awkward file names.
_ILLEGAL_NAME = re.compile(r"[\\/:*?\"<>|\n\r\t]+")


def render_name(pattern, doc):
	"""Render a file-naming pattern like '{{doctype}} - {{name}} - {{supplier_name}}'.

	Tokens resolve against the doc's fields, plus two synthetics: `{{doctype}}`
	and `{{name}}`. Unknown/blank tokens collapse to empty, and the result is
	sanitised and length-clamped for Drive.
	"""
	pattern = pattern or "{{doctype}} - {{name}}"
	context = {
		"doctype": getattr(doc, "doctype", ""),
		"name": getattr(doc, "name", ""),
	}

	def _sub(match):
		key = match.group(1).strip()
		if key in context:
			value = context[key]
		else:
			value = doc.get(key) if hasattr(doc, "get") else getattr(doc, key, "")
		return "" if value is None else str(value)

	rendered = re.sub(r"\{\{\s*([\w.]+)\s*\}\}", _sub, pattern)
	return sanitize_filename(rendered)


def sanitize_filename(name):
	name = _ILLEGAL_NAME.sub(" ", name or "").strip()
	# Collapse the runs of spaces / dangling separators a blank token leaves behind.
	name = re.sub(r"\s+", " ", name)
	name = re.sub(r"(\s-\s)+", " - ", name).strip(" -")
	if not name:
		name = "Document"
	# Drive's hard limit is generous; keep well under it for readability.
	return name[:200]
