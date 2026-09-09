# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Thin Google Docs v1 client + document-structure helpers.

Two responsibilities:

1. Talk to the API (`get_document`, `batch_update`).
2. Read the returned document JSON — a tree of structural elements with absolute
   character indexes — so the renderer can locate placeholders, table markers and
   this app's managed named ranges, and turn them into index-based edit requests.

Google Docs indexes: every character in the body has an absolute index. Edits
shift everything after them, so the renderer always applies a batch's requests
back-to-front (highest index first). The small request-builder helpers here keep
that logic declarative.
"""

import frappe

from playground.playground.google_docs import auth
from playground.playground.google_docs.exceptions import GoogleApiError


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def get_document(document_id):
	svc = auth.docs_service()
	try:
		return svc.documents().get(documentId=document_id).execute()
	except Exception as exc:
		raise _wrap(frappe._("reading the Google Doc"), exc)


def batch_update(document_id, requests):
	"""Apply an ordered list of requests. No-op (and no API call) if empty."""
	if not requests:
		return None
	svc = auth.docs_service()
	try:
		return (
			svc.documents()
			.batchUpdate(documentId=document_id, body={"requests": requests})
			.execute()
		)
	except Exception as exc:
		raise _wrap(frappe._("updating the Google Doc"), exc)


def _wrap(action, exc):
	status = getattr(getattr(exc, "resp", None), "status", None)
	if status == 404:
		msg = frappe._("Google could not find the document while {0} (404).")
	elif status == 403:
		msg = frappe._("Google denied permission while {0} (403).")
	elif status == 429:
		msg = frappe._("Google API quota exceeded while {0} (429).")
	else:
		msg = frappe._("Google Docs error while {0}: ") + str(exc)
	return GoogleApiError(msg.format(action))


# ---------------------------------------------------------------------------
# document reading
# ---------------------------------------------------------------------------

def iter_text_runs(document):
	"""Yield (start_index, end_index, text) for every textRun in the body.

	Recurses into tables so text inside cells is visible too. Order follows the
	document, but callers that edit should still sort by index themselves.
	"""
	body = (document or {}).get("body", {})
	yield from _iter_content(body.get("content", []))


def _iter_content(content):
	for element in content or []:
		if "paragraph" in element:
			for el in element["paragraph"].get("elements", []):
				run = el.get("textRun")
				if run is not None and "content" in run:
					yield el["startIndex"], el["endIndex"], run["content"]
		elif "table" in element:
			for row in element["table"].get("tableRows", []):
				for cell in row.get("tableCells", []):
					yield from _iter_content(cell.get("content", []))


def find_text(document, needle):
	"""Return (start_index, end_index) of the first `needle` within one text run.

	Templates should keep placeholders/markers as plain unformatted text so they
	live in a single run; a placeholder split across runs by mid-word formatting
	is not found here (documented limitation — see README).
	"""
	if not needle:
		return None
	for start, _end, text in iter_text_runs(document):
		pos = text.find(needle)
		if pos != -1:
			return start + pos, start + pos + len(needle)
	return None


def find_marker_paragraph(document, marker):
	"""Locate a table marker that sits alone on a paragraph.

	Returns (paragraph_start, paragraph_end, marker_start, marker_end) where the
	paragraph_* bound the whole paragraph element (so the renderer can drop the
	marker paragraph and insert a table in its place).
	"""
	body = (document or {}).get("body", {})
	for element in body.get("content", []):
		para = element.get("paragraph")
		if not para:
			continue
		text = "".join(
			el.get("textRun", {}).get("content", "") for el in para.get("elements", [])
		)
		pos = text.find(marker)
		if pos != -1:
			base = element["startIndex"]
			return (
				element["startIndex"],
				element["endIndex"],
				base + pos,
				base + pos + len(marker),
			)
	return None


def get_named_range(document, name):
	"""Return the list of {startIndex, endIndex} ranges for a named range, or []."""
	named = (document or {}).get("namedRanges", {})
	# namedRanges is keyed by name -> {namedRanges: [{namedRangeId, ranges:[...]}]}
	entry = named.get(name)
	if not entry:
		return []
	ranges = []
	for nr in entry.get("namedRanges", []):
		ranges.extend(nr.get("ranges", []))
	return ranges


def end_index(document):
	"""Absolute end index of the body (last valid insertion point minus one)."""
	content = (document or {}).get("body", {}).get("content", [])
	return content[-1]["endIndex"] if content else 1


# ---------------------------------------------------------------------------
# request builders (plain dicts — declarative, easy to unit-test)
# ---------------------------------------------------------------------------

def req_replace_all_text(placeholder, value, match_case=True):
	return {
		"replaceAllText": {
			"containsText": {"text": placeholder, "matchCase": match_case},
			"replaceText": value if value is not None else "",
		}
	}


def req_insert_text(index, text):
	return {"insertText": {"location": {"index": index}, "text": text}}


def req_delete_range(start, end):
	return {"deleteContentRange": {"range": {"startIndex": start, "endIndex": end}}}


def req_create_named_range(name, start, end):
	return {
		"createNamedRange": {
			"name": name,
			"range": {"startIndex": start, "endIndex": end},
		}
	}


def req_delete_named_range(name):
	return {"deleteNamedRange": {"name": name}}


def req_insert_table(index, rows, columns):
	return {
		"insertTable": {
			"location": {"index": index},
			"rows": rows,
			"columns": columns,
		}
	}


def req_set_column_width(table_start_index, column_index, width_pt):
	return {
		"updateTableColumnProperties": {
			"tableStartLocation": {"index": table_start_index},
			"columnIndices": [column_index],
			"tableColumnProperties": {
				"widthType": "FIXED_WIDTH",
				"width": {"magnitude": width_pt, "unit": "PT"},
			},
			"fields": "widthType,width",
		}
	}
