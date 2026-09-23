# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Render a received Communication as a PDF and attach it."""

import base64
import mimetypes
import os
import re

import frappe
from frappe.utils import format_datetime
from frappe.utils.pdf import get_pdf

from playground.playground.rfq_intake.utils import clean_subject

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "templates", "email_pdf.html")
_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_PRIVATE_SRC = re.compile(r"""src=(["'])(/private/files/[^"']+)\1""", re.IGNORECASE)


def _inline_private_images(html: str) -> str:
	"""wkhtmltopdf can't fetch login-protected /private/files URLs, so embed them as data URIs."""

	def repl(m):
		url = m.group(2)
		name = frappe.db.get_value("File", {"file_url": url}, "name")
		if not name:
			return m.group(0)
		content = frappe.get_doc("File", name).get_content()
		if isinstance(content, str):
			content = content.encode()
		mime = mimetypes.guess_type(url)[0] or "image/png"
		return f'src="data:{mime};base64,{base64.b64encode(content).decode()}"'

	return _PRIVATE_SRC.sub(repl, html)


def _safe_filename(comm) -> str:
	date = str(comm.communication_date or "")[:10]
	subject = re.sub(r"[^\w\- ]+", "", clean_subject(comm.subject))[:80].strip() or "email"
	return f"{date}_{subject}.pdf"


def attach_email_pdf(comm, attachments: list[dict]) -> dict:
	"""Create the PDF, attach it to the Communication, and return it as a File dict."""
	existing = frappe.db.get_value(
		"File",
		{"attached_to_doctype": "Communication", "attached_to_name": comm.name, "file_name": _safe_filename(comm)},
		["name", "file_name", "file_url", "is_private"],
		as_dict=True,
	)
	if existing:
		return existing

	body = _inline_private_images(_SCRIPT.sub("", comm.content or ""))
	with open(TEMPLATE_PATH, encoding="utf-8") as f:
		template = f.read()
	html = frappe.render_template(
		template,
		{
			"subject": comm.subject,
			"sender": f"{comm.sender_full_name or ''} <{comm.sender}>".strip(),
			"recipients": comm.recipients,
			"cc": comm.cc,
			"date": format_datetime(comm.communication_date),
			"message_id": comm.message_id,
			"body": body,
			"attachments": attachments,
		},
		is_path=False,
	)
	pdf = get_pdf(html, options={"disable-javascript": ""})

	file_doc = frappe.get_doc({
		"doctype": "File",
		"file_name": _safe_filename(comm),
		"content": pdf,
		"is_private": 1,
		"attached_to_doctype": "Communication",
		"attached_to_name": comm.name,
	}).insert(ignore_permissions=True)
	return {"name": file_doc.name, "file_name": file_doc.file_name, "file_url": file_doc.file_url, "is_private": 1}
