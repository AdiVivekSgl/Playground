# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Typed errors for the Google Docs engine.

Every one carries a concise, user-facing `message` (safe to show in the ERP UI)
while the full technical traceback is written to the Frappe Error Log by the API
layer. Section 14 of the spec: errors must be visible AND useful, and a failed
Google creation must never leave the ERP document looking successful.
"""

import frappe


class GoogleDocsError(frappe.ValidationError):
	"""Base class — anything the engine raises that a user should see."""


class ConfigurationError(GoogleDocsError):
	"""Missing/invalid Settings: no service-account key, no default folder, etc."""


class AuthenticationError(GoogleDocsError):
	"""Google rejected the service-account credentials / delegation."""


class TemplateError(GoogleDocsError):
	"""No enabled template for the DocType, or an invalid Google template id."""


class RenderError(GoogleDocsError):
	"""Placeholder not found, bad fieldname, child-table mapping failure, etc."""


class GoogleApiError(GoogleDocsError):
	"""A Drive/Docs API call failed (quota, permission denied, not found …)."""
