# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Orchestration + whitelisted server API.

The single entry point for the form buttons, custom code, workflows and
scheduled jobs (spec 16). Every public function is permission-checked, writes
metadata back onto the ERP document, and records a `Google Document Log` row so
success and failure are both auditable (spec 14-15).

    create_google_document(doctype, name)   ->  copy template, render, store id
    update_google_document(doctype, name)   ->  rewrite managed content only
    get_google_document(doctype, name)      ->  metadata + button state (read)
    open_google_document(doctype, name)     ->  log an 'Opened' action, return URL

Google API work can be slow, so create/update accept `enqueue=1` to run through a
Frappe background job; the ERP document's status field reflects progress and the
user refreshes/opens when done (spec 17). The service layer is identical either
way, so it is equally callable from a scheduler.
"""

import frappe
from frappe.utils import now_datetime

from playground.playground.google_docs import drive, permissions, renderer
from playground.playground.google_docs.auth import get_settings
from playground.playground.google_docs.constants import (
	ACTION_CREATED,
	ACTION_FAILED,
	ACTION_OPENED,
	ACTION_UPDATED,
	META_FIELDS,
	STATUS_CREATED,
	STATUS_ERROR,
	STATUS_UPDATED,
)
from playground.playground.google_docs.exceptions import (
	GoogleDocsError,
	TemplateError,
)


# ---------------------------------------------------------------------------
# whitelisted endpoints
# ---------------------------------------------------------------------------

@frappe.whitelist()
def get_google_document(doctype, name):
	"""Read-only status for the form: does a template exist, is a doc created?"""
	permissions.check_erp_permission(doctype, name, ptype="read")
	template = _find_template(doctype, required=False)
	meta = _read_metadata(doctype, name)
	return {
		"supported": bool(template),
		"template": template.name if template else None,
		"document_id": meta.get("id"),
		"document_url": meta.get("url"),
		"status": meta.get("status"),
		"created_on": meta.get("created_on"),
		"last_updated": meta.get("last_updated"),
	}


@frappe.whitelist()
def create_google_document(doctype, name, force_new=0, enqueue=0):
	"""Create a Google Doc for an ERP document.

	Refuses to create a duplicate: if one already exists, the caller must use
	`update_google_document` or pass `force_new=1` (the 'Create New Google Doc'
	button) to deliberately supersede it. This is the anti-duplicate guard.
	"""
	force_new = frappe.utils.cint(force_new)
	enqueue = frappe.utils.cint(enqueue)
	permissions.check_erp_permission(doctype, name, ptype="write")
	_find_template(doctype, required=True)  # fail fast if unsupported

	existing = _read_metadata(doctype, name).get("id")
	if existing and not force_new:
		raise GoogleDocsError(
			frappe._(
				"A Google Doc already exists for {0} {1}. Use 'Update Google Doc', "
				"or 'Create New Google Doc' to replace it."
			).format(doctype, name)
		)

	return _dispatch("_run_create", doctype, name, enqueue)


@frappe.whitelist()
def update_google_document(doctype, name, enqueue=0):
	"""Rewrite the ERP-controlled content of the existing Google Doc in place."""
	enqueue = frappe.utils.cint(enqueue)
	permissions.check_erp_permission(doctype, name, ptype="write")
	_find_template(doctype, required=True)

	if not _read_metadata(doctype, name).get("id"):
		raise GoogleDocsError(
			frappe._("No Google Doc exists for {0} {1} yet. Create one first.").format(
				doctype, name
			)
		)

	return _dispatch("_run_update", doctype, name, enqueue)


def maybe_auto_create(doc, method=None):
	"""doc_events hook body for 'Automatic on Submit' templates.

	Not wired by default (spec 3: implement manual fully, keep auto architectural).
	To enable, add to hooks.doc_events, e.g.::

	    doc_events = {"Purchase Order": {"on_submit":
	        "playground.playground.google_docs.api.maybe_auto_create"}}

	It runs only when an enabled template for the doctype opts into auto-creation,
	is enqueued, and never blocks submit on a Google failure.
	"""
	try:
		template = _find_template(doc.doctype, required=False)
		if not template or template.creation_behavior != "Automatic on Submit":
			return
		if _read_metadata(doc.doctype, doc.name).get("id"):
			return  # never silently duplicate
		frappe.enqueue(
			"playground.playground.google_docs.api._run_create",
			queue="long",
			timeout=600,
			doctype=doc.doctype,
			name=doc.name,
			user=frappe.session.user,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), f"Google Docs auto-create failed: {doc.doctype} {doc.name}")


@frappe.whitelist()
def open_google_document(doctype, name):
	"""Return the doc URL and log that it was opened. Never creates anything."""
	permissions.check_erp_permission(doctype, name, ptype="read")
	meta = _read_metadata(doctype, name)
	url = meta.get("url")
	if not url:
		raise GoogleDocsError(frappe._("No Google Doc exists for {0} {1}.").format(doctype, name))
	_log(doctype, name, meta.get("id"), ACTION_OPENED, url=url, status="Success")
	return {"url": url}


# ---------------------------------------------------------------------------
# dispatch (sync or background)
# ---------------------------------------------------------------------------

def _dispatch(runner, doctype, name, enqueue):
	if enqueue:
		frappe.enqueue(
			f"playground.playground.google_docs.api.{runner}",
			queue="long",
			timeout=600,
			doctype=doctype,
			name=name,
			user=frappe.session.user,
		)
		return {"queued": True}
	return globals()[runner](doctype, name, user=frappe.session.user)


def _run_create(doctype, name, user=None):
	return _generate(doctype, name, is_update=False, user=user)


def _run_update(doctype, name, user=None):
	return _generate(doctype, name, is_update=True, user=user)


def _generate(doctype, name, is_update, user=None):
	"""Shared create/update body. Runs as `user` so permissions hold in a job."""
	if user and user != frappe.session.user:
		frappe.set_user(user)

	# Re-assert permission (the enqueued path did not go through the endpoint).
	doc = permissions.get_document(doctype, name, ptype="write")
	template = _find_template(doctype, required=True)
	settings = get_settings()
	action = ACTION_UPDATED if is_update else ACTION_CREATED

	try:
		url = None
		if is_update:
			document_id = _read_metadata(doctype, name).get("id")
		else:
			document_id, url = _create_copy(template, settings, doc)

		renderer.render(template, doc, document_id, is_update=is_update)

		# The copy already returned a link; only ask Drive again if we don't have one.
		if not url:
			url = drive.web_view_link(document_id)
		_write_metadata(
			doctype,
			name,
			document_id=document_id,
			url=url,
			status=STATUS_UPDATED if is_update else STATUS_CREATED,
			is_update=is_update,
		)
		_log(doctype, name, document_id, action, url=url, status="Success")
		frappe.db.commit()
		return {"document_id": document_id, "url": url, "status": action}
	except Exception as exc:
		frappe.db.rollback()
		# The ERP doc must NOT look successful on failure (spec 14): flag + log.
		_write_metadata(doctype, name, status=STATUS_ERROR, is_update=is_update)
		message = str(exc) if isinstance(exc, GoogleDocsError) else frappe._(
			"Google Doc {0} failed. See the Error Log for details."
		).format(action.lower())
		_log(doctype, name, _read_metadata(doctype, name).get("id"), ACTION_FAILED,
			status="Error", error=frappe.get_traceback())
		frappe.db.commit()
		frappe.log_error(frappe.get_traceback(), f"Google Docs {action} failed: {doctype} {name}")
		frappe.throw(message, exc=type(exc) if isinstance(exc, GoogleDocsError) else GoogleDocsError)


def _create_copy(template, settings, doc):
	from playground.playground.google_docs.placeholders import render_name

	if not template.google_template_id:
		raise TemplateError(frappe._("Template {0} has no Google Template ID.").format(template.name))

	folder = template.google_drive_folder_id or settings.default_folder_id
	filename = render_name(template.file_naming_pattern, doc)
	created = drive.copy_template(template.google_template_id, filename, folder_id=folder)
	document_id = created["id"]

	# Sharing: default is inherit-folder (no-op). Anything wider is opt-in.
	drive.apply_sharing(
		document_id,
		template.sharing_mode or settings.default_sharing_mode,
		emails=template.share_emails,
		domain=permissions.sharing_domain(),
	)
	return document_id, created.get("webViewLink")


# ---------------------------------------------------------------------------
# template lookup + metadata + logging
# ---------------------------------------------------------------------------

def _find_template(doctype, required=True):
	rows = frappe.get_all(
		"Google Document Template",
		filters={"reference_doctype": doctype, "enabled": 1},
		fields=["name"],
		order_by="modified desc",
		limit=1,
	)
	if not rows:
		if required:
			raise TemplateError(
				frappe._("No enabled Google Document Template exists for {0}.").format(doctype)
			)
		return None
	return frappe.get_cached_doc("Google Document Template", rows[0].name)


def _read_metadata(doctype, name):
	fields = list(META_FIELDS.values())
	# The custom fields may not exist yet on a doctype with no template installed.
	try:
		values = frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
	except Exception:
		values = {}
	return {
		"id": values.get(META_FIELDS["id"]),
		"url": values.get(META_FIELDS["url"]),
		"status": values.get(META_FIELDS["status"]),
		"created_on": values.get(META_FIELDS["created_on"]),
		"last_updated": values.get(META_FIELDS["last_updated"]),
	}


def _write_metadata(doctype, name, document_id=None, url=None, status=None, is_update=False):
	"""Write metadata straight to the DB (works on submitted docs, fires no hooks)."""
	payload = {}
	if document_id is not None:
		payload[META_FIELDS["id"]] = document_id
	if url is not None:
		payload[META_FIELDS["url"]] = url
	if status is not None:
		payload[META_FIELDS["status"]] = status
	now = now_datetime()
	if not is_update and document_id is not None:
		payload[META_FIELDS["created_on"]] = now
	payload[META_FIELDS["last_updated"]] = now
	try:
		frappe.db.set_value(doctype, name, payload, update_modified=False)
	except Exception:
		# Never let a metadata write mask the real outcome.
		frappe.log_error(frappe.get_traceback(), f"Google Docs metadata write failed: {doctype} {name}")


def _log(doctype, name, document_id, action, url=None, status="Success", error=None):
	try:
		log = frappe.new_doc("Google Document Log")
		log.reference_doctype = doctype
		log.reference_document = name
		log.google_document_id = document_id
		log.google_document_url = url
		log.action = action
		log.status = status
		log.error_message = error
		log.log_user = frappe.session.user
		log.timestamp = now_datetime()
		log.insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Google Docs: writing Google Document Log failed")
