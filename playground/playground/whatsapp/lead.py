# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""`lead Name; phone; email; notes` - create an ERPNext Lead from WhatsApp.

	lead Aditya Saigal; +919811546751; adisag@gmail.com; CEO and MD of Frontec, they make cable joints

Parts are separated by ";" (or new lines). The first part is the person's name;
after that, each part is recognised by its shape - an e-mail address, a phone
number, or a labelled field ("company: Frontec", "title: CEO") - and anything
else becomes a note on the Lead. No LLM: nothing is inferred from free text.

The Lead is inserted as the sender's mapped ERPNext user (user_context.as_user)
with a normal permission-checked insert, so it needs Lead "create" permission,
ERPNext's own validation applies (e.g. duplicate e-mail), and the user becomes
the Lead's owner.
"""

import re

import frappe
from frappe.utils import escape_html, get_url_to_form, now_datetime, strip_html_tags

from playground.playground.whatsapp.phone import normalize_phone
from playground.playground.whatsapp.user_context import as_user

MAX_NAME_LENGTH = 140
MAX_FIELD_LENGTH = 140
MAX_NOTE_LENGTH = 2000
ERROR_TEXT_LIMIT = 300

_SEPARATORS = re.compile(r"[;\n]")
_EMAIL_RE = re.compile(r"[^@\s;,]+@[^@\s;,]+\.[A-Za-z]{2,}")
# Only digits, spaces and + ( ) . - : the whole part must look like a number.
_PHONE_RE = re.compile(r"\+?[\d\s().-]{8,}")
_LABEL_RE = re.compile(r"(?P<label>[A-Za-z ]{2,20}?)\s*:\s*(?P<value>.+)", re.DOTALL)
# Recognised "label: value" parts -> Lead field.
_LABELS = {
	"company": "company_name",
	"organisation": "company_name",
	"organization": "company_name",
	"org": "company_name",
	"title": "job_title",
	"designation": "job_title",
	"job title": "job_title",
	"email": "email_id",
	"e-mail": "email_id",
	"mobile": "mobile_no",
	"phone": "mobile_no",
	"note": "notes",
	"notes": "notes",
}

_FIELD_NAMES = {"email_id": "e-mail", "mobile_no": "phone number", "company_name": "company", "job_title": "title"}

USAGE_REPLY = (
	"To add a lead, send:\n"
	"*lead Name; phone; email; notes*\n"
	"e.g. lead Aditya Saigal; +919811546751; adisag@gmail.com; CEO of Frontec, makes cable joints\n"
	"Optional: company: …; title: …"
)
NOT_LINKED_REPLY = (
	"Sorry, your WhatsApp number is not linked to an ERPNext user, so I can't create leads. "
	"Please ask your ERPNext administrator to add it under *WhatsApp User*."
)
PERMISSION_DENIED_REPLY = "Sorry, your ERPNext user doesn't have permission to create Leads."
REJECTED_REPLY = "Sorry, the lead was not created:\n{error}"
BAD_PART_REPLY = "Sorry, the lead was not created: {reason}\n\n" + USAGE_REPLY


def parse_lead(args):
	"""Return (fields, notes) from the text after "lead", or raise ValueError(reason)."""
	parts = [" ".join(p.split()) for p in _SEPARATORS.split(args or "")]
	parts = [p for p in parts if p]
	if not parts:
		raise ValueError("no name given.")

	name = parts[0]
	if _EMAIL_RE.fullmatch(name) or _looks_like_phone(name):
		raise ValueError("the first part must be the person's name.")
	if len(name) > MAX_NAME_LENGTH:
		raise ValueError(f"the name is longer than {MAX_NAME_LENGTH} characters.")
	first, _, last = name.partition(" ")
	fields = {"first_name": first, "last_name": last or None}
	notes = []

	for part in parts[1:]:
		label = _LABEL_RE.fullmatch(part)
		fieldname = _LABELS.get(label.group("label").strip().casefold()) if label else None
		value = label.group("value").strip() if fieldname else part

		if fieldname == "notes":
			notes.append(value)
		elif fieldname in ("email_id", None) and _EMAIL_RE.fullmatch(value):
			_set_once(fields, "email_id", value.lower())
		elif fieldname in ("mobile_no", None) and _looks_like_phone(value):
			_set_once(fields, "mobile_no", "+" + normalize_phone(value))
		elif fieldname in ("email_id", "mobile_no"):
			raise ValueError(f"'{_clip(value, 60)}' is not a valid {'e-mail' if fieldname == 'email_id' else 'phone number'}.")
		elif fieldname:
			if len(value) > MAX_FIELD_LENGTH:
				raise ValueError(f"{label.group('label').strip()} is longer than {MAX_FIELD_LENGTH} characters.")
			_set_once(fields, fieldname, value)
		else:
			notes.append(part)

	note = "\n".join(notes)
	if len(note) > MAX_NOTE_LENGTH:
		raise ValueError(f"the notes are longer than {MAX_NOTE_LENGTH} characters.")
	return {k: v for k, v in fields.items() if v}, note


def create_lead(args, sender):
	if not (args or "").strip():
		return USAGE_REPLY
	if not sender or not sender.user:
		return NOT_LINKED_REPLY
	try:
		fields, note = parse_lead(args)
	except ValueError as e:
		return BAD_PART_REPLY.format(reason=e)

	with as_user(sender.user) as user:
		doc = frappe.get_doc({"doctype": "Lead", **fields})
		# ERPNext v14+ Leads have a Notes table (CRM Note); older ones get a timeline comment.
		notes_table = bool(note) and frappe.get_meta("Lead").has_field("notes")
		if notes_table:
			doc.append("notes", {"note": _note_html(note), "added_by": user, "added_on": now_datetime()})
		# Savepoint: a rejected insert must leave nothing behind, because the
		# request itself still commits (the WhatsApp Query Log update).
		frappe.db.savepoint("wa_lead")
		try:
			doc.insert()  # permission-checked, as the sender's user
			if note and not notes_table:
				doc.add_comment("Comment", _note_html(note))
		except frappe.PermissionError:
			frappe.db.rollback(save_point="wa_lead")
			return PERMISSION_DENIED_REPLY
		except frappe.ValidationError as e:
			# ERPNext's own reasons: duplicate e-mail, invalid phone, missing name...
			frappe.db.rollback(save_point="wa_lead")
			return REJECTED_REPLY.format(error=_error_text(e))
		finally:
			# frappe.throw queues desk popups; WhatsApp replies don't use them.
			frappe.clear_messages()

	return format_created_reply(doc, note)


def format_created_reply(doc, note):
	lines = [f"✅ Lead created - *{doc.name}*", f"👤 {doc.lead_name or doc.first_name}"]
	if doc.get("job_title") or doc.get("company_name"):
		lines.append("🏢 " + ", ".join(v for v in (doc.get("job_title"), doc.get("company_name")) if v))
	if doc.get("mobile_no"):
		lines.append(f"📞 {doc.mobile_no}")
	if doc.get("email_id"):
		lines.append(f"✉️ {doc.email_id}")
	if note:
		lines.append(f"📝 {_clip(note, 200)}")
	lines.append(get_url_to_form("Lead", doc.name))
	return "\n".join(lines)


def _looks_like_phone(text):
	return bool(_PHONE_RE.fullmatch(text)) and normalize_phone(text) is not None


def _set_once(fields, fieldname, value):
	if fields.get(fieldname) and fields[fieldname] != value:
		raise ValueError(f"more than one {_FIELD_NAMES.get(fieldname, fieldname)} given.")
	fields[fieldname] = value


def _note_html(note):
	return "<br>".join(escape_html(line) for line in note.split("\n"))


def _clip(text, limit):
	return text if len(text) <= limit else text[: limit - 1] + "…"


def _error_text(exc):
	text = " ".join(strip_html_tags(str(exc) or "").split())
	return _clip(text or "please check the details.", ERROR_TEXT_LIMIT)
