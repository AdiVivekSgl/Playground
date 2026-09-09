# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Shared constants for the Google Docs rendering engine."""

# Google API scopes. Drive covers copy/move/rename/share of the generated file;
# Documents covers the batchUpdate rendering. `drive.file` would be narrower but
# cannot copy a template the app did not itself create, so we need full `drive`.
SCOPES = [
	"https://www.googleapis.com/auth/documents",
	"https://www.googleapis.com/auth/drive",
]

# Prefix for every named range this app manages inside a generated Google Doc.
# Named ranges are how we identify ERP-controlled content on update without
# touching text the user added by hand (see placeholders.py / renderer.py).
MANAGED_PREFIX = "erpgdoc"

# Default placeholder syntax in the template, e.g. {{supplier_name}}.
PLACEHOLDER_OPEN = "{{"
PLACEHOLDER_CLOSE = "}}"

# Metadata custom fields added to every reference DocType (see setup.py). The
# `custom_` prefix is required for Custom Fields on standard ERPNext doctypes.
META_FIELDS = {
	"id": "custom_google_document_id",
	"url": "custom_google_document_url",
	"created_on": "custom_google_document_created_on",
	"last_updated": "custom_google_document_last_updated",
	"status": "custom_google_document_status",
}

# Values for the metadata status Select field.
STATUS_NOT_CREATED = "Not Created"
STATUS_CREATED = "Created"
STATUS_UPDATED = "Updated"
STATUS_ERROR = "Error"

STATUS_OPTIONS = [STATUS_NOT_CREATED, STATUS_CREATED, STATUS_UPDATED, STATUS_ERROR]

# Google Document Log action values.
ACTION_CREATED = "Created"
ACTION_UPDATED = "Updated"
ACTION_OPENED = "Opened"
ACTION_FAILED = "Failed"

# Sharing modes offered by Google Document Template / Settings.
SHARE_INHERIT = "Inherit Folder"
SHARE_PRIVATE = "Private"
SHARE_COMPANY = "Company (Domain)"
SHARE_SPECIFIC = "Specific Emails"
SHARE_ANYONE = "Anyone With Link"
