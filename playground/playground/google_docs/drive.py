# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Thin Google Drive v3 client.

Only the operations the engine needs: copy a template, move the copy into the
configured folder, rename it, and apply sharing. Every call sets
`supportsAllDrives=True` so Shared Drives work identically to My Drive.

All Google exceptions are normalised to GoogleApiError with a concise message
(the caller logs the full traceback).
"""

import frappe

from playground.playground.google_docs import auth
from playground.playground.google_docs.constants import (
	SHARE_ANYONE,
	SHARE_COMPANY,
	SHARE_SPECIFIC,
)
from playground.playground.google_docs.exceptions import GoogleApiError


def _wrap(action, exc):
	# googleapiclient raises HttpError with a .resp.status; surface it cleanly.
	status = getattr(getattr(exc, "resp", None), "status", None)
	if status == 404:
		msg = frappe._("Google could not find the file/folder while {0} (404). Check the id.")
	elif status == 403:
		msg = frappe._("Google denied permission while {0} (403). Check sharing/quota.")
	elif status == 429:
		msg = frappe._("Google API quota exceeded while {0} (429). Try again shortly.")
	else:
		msg = frappe._("Google Drive error while {0}: ") + str(exc)
	return GoogleApiError(msg.format(action))


def copy_template(template_id, name, folder_id=None, shared_drive_id=None):
	"""Copy the Docs template into `folder_id`, return the new file's metadata.

	The copy is what gets rendered — the template itself is never modified, so its
	fonts / header / footer / logo / page setup are preserved by construction.
	"""
	svc = auth.drive_service()
	body = {"name": name}
	if folder_id:
		body["parents"] = [folder_id]
	try:
		return (
			svc.files()
			.copy(
				fileId=template_id,
				body=body,
				fields="id, name, webViewLink, parents",
				supportsAllDrives=True,
			)
			.execute()
		)
	except Exception as exc:
		raise _wrap(frappe._("copying the template"), exc)


def rename_file(file_id, name):
	svc = auth.drive_service()
	try:
		return (
			svc.files()
			.update(fileId=file_id, body={"name": name}, fields="id, name", supportsAllDrives=True)
			.execute()
		)
	except Exception as exc:
		raise _wrap(frappe._("renaming the document"), exc)


def web_view_link(file_id):
	svc = auth.drive_service()
	try:
		meta = (
			svc.files()
			.get(fileId=file_id, fields="id, webViewLink", supportsAllDrives=True)
			.execute()
		)
		return meta.get("webViewLink")
	except Exception as exc:
		raise _wrap(frappe._("reading the document link"), exc)


def apply_sharing(file_id, mode, emails=None, domain=None):
	"""Apply the configured sharing mode to the generated file.

	`Inherit Folder` (the default) is a no-op: the copy inherits the folder's
	permissions automatically, and we never widen access beyond that. We never
	default to public — SHARE_ANYONE only happens when explicitly configured.
	"""
	if not mode or mode == "Inherit Folder":
		return
	svc = auth.drive_service()
	permissions = []
	if mode == SHARE_ANYONE:
		permissions.append({"type": "anyone", "role": "reader"})
	elif mode == SHARE_COMPANY and domain:
		permissions.append({"type": "domain", "role": "reader", "domain": domain})
	elif mode == SHARE_SPECIFIC:
		for email in _split_emails(emails):
			permissions.append({"type": "user", "role": "writer", "emailAddress": email})
	# SHARE_PRIVATE: create no permissions (owner/impersonated user only).

	for perm in permissions:
		try:
			svc.permissions().create(
				fileId=file_id,
				body=perm,
				sendNotificationEmail=False,
				supportsAllDrives=True,
				fields="id",
			).execute()
		except Exception as exc:
			# Sharing failures are logged but must not fail an otherwise-successful
			# document creation — the doc exists; the admin can fix sharing after.
			frappe.log_error(
				frappe.get_traceback(),
				f"Google Docs: sharing '{perm}' on {file_id} failed",
			)


def _split_emails(emails):
	if not emails:
		return []
	if isinstance(emails, str):
		emails = emails.replace("\n", ",").split(",")
	return [e.strip() for e in emails if e and e.strip()]
