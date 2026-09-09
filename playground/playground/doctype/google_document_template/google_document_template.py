# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

import re

import frappe
from frappe.model.document import Document

# docs.google.com/document/d/<ID>/edit  |  drive.google.com/.../d/<ID>/...
_DOC_ID = re.compile(r"/d/([a-zA-Z0-9_-]{20,})")
_FOLDER_ID = re.compile(r"/folders/([a-zA-Z0-9_-]{20,})")


class GoogleDocumentTemplate(Document):
	def validate(self):
		self._extract_ids()
		self._validate_markers()

	def on_update(self):
		# Enabling a template for a DocType provisions its metadata fields right
		# away, so the form buttons work without waiting for the next migrate.
		if self.enabled and self.reference_doctype:
			from playground.playground.google_docs.setup import ensure_metadata_fields

			ensure_metadata_fields(self.reference_doctype)

	def _extract_ids(self):
		if self.google_template_url and not self.google_template_id:
			match = _DOC_ID.search(self.google_template_url)
			if match:
				self.google_template_id = match.group(1)
		if self.google_drive_folder_id:
			match = _FOLDER_ID.search(self.google_drive_folder_id)
			if match:
				# Someone pasted a full folder URL into the ID field — extract it.
				self.google_drive_folder_id = match.group(1)

	def _validate_markers(self):
		"""Every column's Table Marker must match a declared Table Mapping."""
		declared = {(t.table_marker or "").strip() for t in self.table_mappings}
		for col in self.column_mappings:
			marker = (col.table_marker or "").strip()
			if marker and marker not in declared:
				frappe.throw(
					frappe._(
						"Column '{0}' references table marker {1}, which is not in the "
						"Tables grid."
					).format(col.child_fieldname or col.column_label, marker)
				)
