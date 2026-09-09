# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Document renderer: Frappe document -> Google Docs edit operations.

This is the engine. Given a `Google Document Template` config, a source Frappe
doc, and a (freshly copied) Google Doc id, it:

* replaces scalar placeholders ({{supplier_name}} ...) with formatted values, and
* renders each configured child table at its marker ({{items_table}} ...).

Managed sections (spec 10-11): every piece of ERP-controlled content the engine
writes is wrapped in a Google Docs *named range* (`erpgdoc:field:*` /
`erpgdoc:table:*`). `Update` rewrites exactly those ranges and nothing else, so
notes/comments/text the user added to the doc by hand survive untouched.

Index safety: Google Docs edits shift every index after them. Scalar edits are
therefore applied highest-index-first in a single batch; structural table edits
re-read the document between steps.
"""

import frappe

from playground.playground.google_docs import docs, formatting, placeholders
from playground.playground.google_docs.exceptions import RenderError


def render(template, doc, document_id, is_update=False):
	"""Populate `document_id` from `doc` per `template`. Returns a summary dict."""
	currency = _resolve_currency(doc)

	scalar_count = _render_scalars(template, doc, document_id, is_update, currency)

	table_count = 0
	for table_map in template.get("table_mappings", []):
		_render_table(template, table_map, doc, document_id, is_update, currency)
		table_count += 1

	return {"fields": scalar_count, "tables": table_count}


# ---------------------------------------------------------------------------
# scalars
# ---------------------------------------------------------------------------

def _render_scalars(template, doc, document_id, is_update, currency):
	mappings = template.get("field_mappings", [])
	if not mappings:
		return 0

	document = docs.get_document(document_id)

	# Build one (start, end, value, range_name) target per mapping, then apply
	# them highest-index-first so earlier edits never invalidate later indexes.
	targets = []
	for m in mappings:
		fieldname = (m.get("erp_fieldname") or "").strip()
		if not fieldname:
			continue
		placeholder = placeholders.normalise_placeholder(m.get("google_placeholder"), fieldname)
		range_name = placeholders.field_range_name(fieldname)

		value = _field_value(doc, fieldname, m, currency)

		span = None
		if is_update:
			ranges = docs.get_named_range(document, range_name)
			if ranges:
				span = (ranges[0]["startIndex"], ranges[0]["endIndex"])
		if span is None:
			# First render, or a mapping added after creation: fall back to the
			# literal placeholder still sitting in the template copy.
			span = docs.find_text(document, placeholder)

		if span is None:
			if m.get("required"):
				raise RenderError(
					frappe._("Required placeholder {0} was not found in the template.").format(
						placeholder
					)
				)
			continue

		targets.append((span[0], span[1], value, range_name))

	if not targets:
		return 0

	requests = []
	for start, end, value, range_name in sorted(targets, key=lambda t: t[0], reverse=True):
		# Clear any previous managed range definition, wipe the old span, insert
		# the new value, then re-mark it as managed.
		if is_update:
			requests.append(docs.req_delete_named_range(range_name))
		if end > start:
			requests.append(docs.req_delete_range(start, end))
		if value:
			requests.append(docs.req_insert_text(start, value))
			requests.append(docs.req_create_named_range(range_name, start, start + len(value)))

	docs.batch_update(document_id, requests)
	return len(targets)


def _field_value(doc, fieldname, mapping, currency):
	# Dotted fieldnames (e.g. supplier_address.address_line1) are resolved one hop
	# via the linked doc where the field is a Link.
	raw = _read_field(doc, fieldname)
	return formatting.format_value(
		raw,
		fieldtype=mapping.get("field_type") or _guess_fieldtype(doc, fieldname),
		transform=mapping.get("formatting"),
		currency=currency,
		default=mapping.get("default_value"),
	)


def _read_field(doc, fieldname):
	if "." in fieldname:
		head, tail = fieldname.split(".", 1)
		link_value = doc.get(head)
		meta_field = doc.meta.get_field(head) if getattr(doc, "meta", None) else None
		if link_value and meta_field and meta_field.fieldtype == "Link":
			try:
				return frappe.db.get_value(meta_field.options, link_value, tail)
			except Exception:
				return None
		return None
	return doc.get(fieldname)


def _guess_fieldtype(doc, fieldname):
	meta = getattr(doc, "meta", None)
	if meta and "." not in fieldname:
		df = meta.get_field(fieldname)
		if df:
			return df.fieldtype
	return "Data"


# ---------------------------------------------------------------------------
# child tables
# ---------------------------------------------------------------------------

def _render_table(template, table_map, doc, document_id, is_update, currency):
	marker = (table_map.get("table_marker") or "").strip()
	table_fieldname = (table_map.get("child_table_fieldname") or "").strip()
	if not marker or not table_fieldname:
		return

	columns = _columns_for(template, table_map)
	if not columns:
		raise RenderError(
			frappe._("Table mapping {0} has no visible columns configured.").format(marker)
		)

	rows = _table_rows(doc, table_fieldname, columns, currency)
	range_name = placeholders.table_range_name(table_fieldname)

	document = docs.get_document(document_id)
	insert_index = None

	if is_update:
		ranges = docs.get_named_range(document, range_name)
		if ranges:
			start, end = ranges[0]["startIndex"], ranges[0]["endIndex"]
			# Remove the old managed table wholesale, then rebuild in its place.
			docs.batch_update(
				document_id,
				[docs.req_delete_named_range(range_name), docs.req_delete_range(start, end)],
			)
			insert_index = start

	if insert_index is None:
		# Create path (or update where the range was missing): consume the marker.
		found = docs.find_marker_paragraph(document, marker)
		if not found:
			if is_update:
				return  # nothing to update and no marker to (re)seed from
			raise RenderError(
				frappe._("Table marker {0} was not found in the template.").format(marker)
			)
		_para_start, _para_end, m_start, m_end = found
		docs.batch_update(document_id, [docs.req_delete_range(m_start, m_end)])
		insert_index = m_start

	table_start, table_end = _write_table(document_id, insert_index, columns, rows)
	docs.batch_update(
		document_id, [docs.req_create_named_range(range_name, table_start, table_end)]
	)


def _columns_for(template, table_map):
	"""Ordered, visible column configs for a table mapping.

	Columns live in the template's flat `column_mappings` child table, tagged with
	the table marker they belong to (Frappe grids can't nest), so N tables are
	supported. Rows with `hidden` set are dropped.
	"""
	marker = (table_map.get("table_marker") or "").strip()
	cols = []
	for c in template.get("column_mappings", []):
		if (c.get("table_marker") or "").strip() != marker:
			continue
		if c.get("hidden"):
			continue
		cols.append(c)
	cols.sort(key=lambda c: c.get("idx") or 0)
	return cols


def _table_rows(doc, table_fieldname, columns, currency):
	"""Return [header_labels, *body_rows] as lists of strings."""
	header = [c.get("column_label") or c.get("child_fieldname") or "" for c in columns]
	body = []
	for child in doc.get(table_fieldname) or []:
		row = []
		for c in columns:
			field = (c.get("child_fieldname") or "").strip()
			raw = child.get(field) if field else ""
			row.append(
				formatting.format_value(
					raw,
					fieldtype=c.get("field_type"),
					transform=c.get("formatting"),
					currency=currency,
					default=c.get("default_value"),
				)
			)
		body.append(row)
	return [header] + body


def _write_table(document_id, insert_index, columns, rows):
	"""Insert a Google Docs table at `insert_index` and fill it.

	Returns the (start, end) index span of the finished table for named-ranging.
	Cells are filled highest-index-first so earlier inserts don't shift later
	cells within the same batch.
	"""
	n_rows = len(rows)
	n_cols = len(columns)
	docs.batch_update(document_id, [docs.req_insert_table(insert_index, n_rows, n_cols)])

	document = docs.get_document(document_id)
	table = _find_table_after(document, insert_index)
	if not table:
		raise RenderError(frappe._("Google did not return the inserted table to fill."))

	cell_indexes = _cell_insert_indexes(table["element"])  # row-major list of ints

	fills = []
	flat = [(r, c) for r in range(n_rows) for c in range(n_cols)]
	# Reverse so we insert into later cells first (their indexes stay valid).
	for r, c in reversed(flat):
		text = rows[r][c] if r < len(rows) and c < len(rows[r]) else ""
		if not text:
			continue
		idx = cell_indexes[r * n_cols + c]
		fills.append(docs.req_insert_text(idx, text))
	docs.batch_update(document_id, fills)

	# Best-effort fixed column widths (points), applied after fills so the table
	# start index is stable. Re-read to get the definitive table bounds.
	document = docs.get_document(document_id)
	table = _find_table_after(document, insert_index)
	width_reqs = []
	for i, col in enumerate(columns):
		width = col.get("width")
		if width:
			width_reqs.append(docs.req_set_column_width(table["start"], i, float(width)))
	if width_reqs:
		docs.batch_update(document_id, width_reqs)
		document = docs.get_document(document_id)
		table = _find_table_after(document, insert_index)

	return table["start"], table["end"]


def _find_table_after(document, index):
	"""First table structural element at/after `index`; None if none."""
	for element in (document or {}).get("body", {}).get("content", []):
		if "table" in element and element["startIndex"] >= index:
			return {
				"element": element,
				"start": element["startIndex"],
				"end": element["endIndex"],
			}
	return None


def _cell_insert_indexes(table_element):
	"""Row-major list of the text insertion index for each cell of a table."""
	indexes = []
	for row in table_element["table"].get("tableRows", []):
		for cell in row.get("tableCells", []):
			content = cell.get("content", [])
			# A freshly inserted cell holds one empty paragraph; its startIndex is
			# where cell text goes.
			indexes.append(content[0]["startIndex"] if content else cell["startIndex"] + 1)
	return indexes


def _resolve_currency(doc):
	currency = doc.get("currency")
	if currency:
		return currency
	company = doc.get("company")
	if company:
		try:
			return frappe.get_cached_value("Company", company, "default_currency")
		except Exception:
			pass
	return frappe.defaults.get_global_default("currency")
