# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Google authentication.

V1 uses a Google Workspace **service account** with (optional) domain-wide
delegation. The service-account JSON key is stored encrypted in the single
`Google Docs Settings` doctype (a Password field, so it is never exposed to the
client and is encrypted at rest by Frappe). Nothing here is ever hard-coded.

Delegation: if `impersonate_user` is set on Settings, the credentials are scoped
to act *as* that Workspace user (so files are owned by a real person / land in
their My Drive or a Shared Drive they can access). Without it, files are owned by
the service account itself — which has no Drive quota of its own, so in that mode
a Shared Drive folder is effectively required.

The google-api-python-client / google-auth packages are imported lazily so that
merely importing this module (e.g. during `bench migrate`) never hard-fails on a
site that has not installed them yet.
"""

import json

import frappe

from playground.playground.google_docs.constants import SCOPES
from playground.playground.google_docs.exceptions import (
	AuthenticationError,
	ConfigurationError,
)

SETTINGS_DOCTYPE = "Google Docs Settings"


def get_settings():
	"""Return the cached single Settings doc, or raise a friendly config error."""
	try:
		return frappe.get_cached_doc(SETTINGS_DOCTYPE)
	except Exception as exc:  # pragma: no cover - only if the doctype is missing
		raise ConfigurationError(
			frappe._("Google Docs Settings is not configured yet.")
		) from exc


def _load_google_libs():
	try:
		from google.oauth2 import service_account  # noqa: WPS433
		from googleapiclient.discovery import build  # noqa: WPS433
	except ImportError as exc:
		raise ConfigurationError(
			frappe._(
				"Google API libraries are not installed. Run "
				"`bench pip install google-api-python-client google-auth` and restart."
			)
		) from exc
	return service_account, build


def _credentials():
	"""Build service-account Credentials from the encrypted JSON key on Settings."""
	settings = get_settings()
	# get_password decrypts the stored Password field; never logged, never sent
	# to the client.
	raw_key = settings.get_password("service_account_json", raise_exception=False)
	if not raw_key:
		raise ConfigurationError(
			frappe._(
				"No service-account key configured. Paste the Workspace "
				"service-account JSON into Google Docs Settings."
			)
		)

	try:
		info = json.loads(raw_key)
	except (ValueError, TypeError) as exc:
		raise ConfigurationError(
			frappe._("The service-account key is not valid JSON.")
		) from exc

	service_account, _build = _load_google_libs()
	try:
		creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
	except Exception as exc:
		raise AuthenticationError(
			frappe._("Could not load the service-account credentials: {0}").format(exc)
		) from exc

	impersonate = (settings.impersonate_user or "").strip()
	if impersonate:
		# Domain-wide delegation: act as this Workspace user.
		creds = creds.with_subject(impersonate)
	return creds


def _service(api, version):
	_service_account, build = _load_google_libs()
	try:
		return build(api, version, credentials=_credentials(), cache_discovery=False)
	except AuthenticationError:
		raise
	except Exception as exc:
		raise AuthenticationError(
			frappe._("Google authentication failed while building the {0} client: {1}").format(
				api, exc
			)
		) from exc


def drive_service():
	"""Authorised Google Drive v3 client."""
	return _service("drive", "v3")


def docs_service():
	"""Authorised Google Docs v1 client."""
	return _service("docs", "v1")


def test_connection():
	"""Cheap round-trip used by the Settings 'Test Connection' button.

	Lists a single file so a misconfigured key / delegation surfaces immediately
	rather than on the first real document creation.
	"""
	svc = drive_service()
	try:
		svc.files().list(pageSize=1, fields="files(id)", supportsAllDrives=True).execute()
	except Exception as exc:
		raise AuthenticationError(
			frappe._("Google connection test failed: {0}").format(exc)
		) from exc
	return True
