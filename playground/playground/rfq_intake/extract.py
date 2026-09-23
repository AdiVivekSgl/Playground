# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Turn a received email + its attachments into structured RFQ data using Claude."""

import base64
import datetime
import json

import anthropic
import frappe
from frappe.core.utils import html2text

from playground.playground.rfq_intake.utils import docx_to_text, xlsx_to_text

MAX_TOTAL_BYTES = 20 * 1024 * 1024  # stay well under the 32 MB request limit
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}
TEXT_TYPES = {".txt", ".csv", ".md", ".eml", ".html", ".htm", ".xml", ".json"}


def _nullable(schema: dict) -> dict:
	return {"anyOf": [schema, {"type": "null"}]}


SCHEMA = {
	"type": "object",
	"additionalProperties": False,
	"required": [
		"doc_type", "title", "customer_name", "contact_name", "customer_email", "territory_hint",
		"territory", "target_date", "overall_value", "currency", "value_source", "line_items",
		"our_reference", "notes", "confidence",
	],
	"properties": {
		"doc_type": {"type": "string", "enum": ["customer_rfq", "our_quotation", "other"]},
		"title": {"type": "string"},
		"customer_name": _nullable({"type": "string"}),
		"contact_name": _nullable({"type": "string"}),
		"customer_email": _nullable({"type": "string"}),
		"territory_hint": _nullable({"type": "string"}),
		"territory": _nullable({"type": "string"}),
		"target_date": _nullable({"type": "string"}),
		"overall_value": _nullable({"type": "number"}),
		"currency": _nullable({"type": "string"}),
		"value_source": {"type": "string", "enum": ["quoted", "estimated_from_line_items", "budget_stated", "not_stated"]},
		"line_items": {
			"type": "array",
			"items": {
				"type": "object",
				"additionalProperties": False,
				"required": ["description", "item_code", "qty", "uom", "rate"],
				"properties": {
					"description": {"type": "string"},
					"item_code": _nullable({"type": "string"}),
					"qty": _nullable({"type": "number"}),
					"uom": _nullable({"type": "string"}),
					"rate": _nullable({"type": "number"}),
				},
			},
		},
		"our_reference": _nullable({"type": "string"}),
		"notes": _nullable({"type": "string"}),
		"confidence": {"type": "number"},
	},
}

SYSTEM_PROMPT = """You extract sales data from emails that a salesperson at {company} has forwarded into an intake mailbox.

The email you see was FORWARDED. The forwarder works at {company} (domains: {own_domains}) and is NOT the customer. \
Find the original sender inside the forwarded header block ("---------- Forwarded message ---------", "From:", "Sent:", etc.). \
If there are several nested forwards, the customer is the earliest external party.

Classify doc_type:
- customer_rfq: a customer is asking {company} for a price, quote, proposal or tender.
- our_quotation: {company} is sending a quotation/proposal to a customer (the forwarder is sharing our own quote).
- other: anything else.

Fields:
- title: a short opportunity title, e.g. "Acme - 40x VFD panels for Plant 3". Do not include "Fwd:" or "RFQ:".
- customer_name: the customer's company name (not a person, not {company}).
- territory: pick EXACTLY one value from this list, or null if you cannot tell: {territories}. \
Base it on the customer's address, signature, phone country code, delivery location or domain TLD. Put the raw location in territory_hint.
- target_date: ISO YYYY-MM-DD. For an RFQ, use the quote submission deadline if there is one, otherwise the required delivery date. \
For our quotation, use the quote validity date or the customer's decision date. Resolve relative dates against the email date. null if absent.
- overall_value: the total value as a number without currency symbols, excluding tax if both are shown. \
Use the stated total or budget if there is one. Otherwise sum qty*rate over line items and set value_source to estimated_from_line_items. \
If there are no prices at all, use null with value_source not_stated. Never guess a value.
- currency: ISO 4217 code, or null.
- our_reference: the RFQ, tender, enquiry or quote number, if there is one.
- line_items: up to 50 of the most significant items requested or quoted.
- notes: one or two sentences on anything a salesperson should know (special terms, site visit, incoterms). null if nothing.
- confidence: 0-1, your confidence that customer_name, target_date and overall_value are all correct.

Today's date is {today}."""


def _attachment_blocks(files: list[dict]) -> tuple[list[dict], list[str]]:
	"""Build Claude content blocks from File docs. Returns (blocks, names of skipped files)."""
	blocks, skipped, total = [], [], 0
	for f in files:
		name = f["file_name"] or ""
		ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
		try:
			content = frappe.get_doc("File", f["name"]).get_content()
			if isinstance(content, str):
				content = content.encode()
		except Exception:
			skipped.append(f"{name} (unreadable)")
			continue
		if total + len(content) > MAX_TOTAL_BYTES:
			skipped.append(f"{name} (size limit)")
			continue
		total += len(content)

		if ext == ".pdf":
			blocks.append({
				"type": "document",
				"source": {"type": "base64", "media_type": "application/pdf", "data": base64.b64encode(content).decode()},
				"title": name,
			})
		elif ext in IMAGE_TYPES:
			blocks.append({
				"type": "image",
				"source": {"type": "base64", "media_type": IMAGE_TYPES[ext], "data": base64.b64encode(content).decode()},
			})
		elif ext in (".xlsx", ".xlsm"):
			blocks.append({"type": "text", "text": f"## Attachment: {name}\n{xlsx_to_text(content)}"})
		elif ext == ".docx":
			blocks.append({"type": "text", "text": f"## Attachment: {name}\n{docx_to_text(content)}"})
		elif ext in TEXT_TYPES:
			text = content.decode("utf-8", errors="ignore")
			if ext in (".html", ".htm"):
				text = html2text(text)
			blocks.append({"type": "text", "text": f"## Attachment: {name}\n{text}"})
		else:
			skipped.append(f"{name} (unsupported type)")
	return blocks, skipped


def extract(comm, files: list[dict], settings, territories: list[str], own_domains: set[str]) -> dict:
	company = settings.company
	system = SYSTEM_PROMPT.format(
		company=company,
		own_domains=", ".join(sorted(own_domains)) or "-",
		territories=json.dumps(territories),
		today=datetime.date.today().isoformat(),
	)

	blocks, skipped = _attachment_blocks(files)
	body = html2text(comm.content or "")
	email_text = (
		f"Forwarded by: {comm.sender}\n"
		f"Received: {comm.communication_date}\n"
		f"Subject: {comm.subject}\n"
		f"Attachments: {', '.join(f['file_name'] for f in files) or 'none'}\n"
		+ (f"Not readable (ignore): {', '.join(skipped)}\n" if skipped else "")
		+ f"\n--- EMAIL BODY ---\n{body}"
	)
	# Documents first, then the instructions/email text.
	content = blocks + [{"type": "text", "text": email_text}]

	client = anthropic.Anthropic(api_key=settings.get_password("anthropic_api_key"))
	kwargs = dict(
		model=settings.model,
		max_tokens=16000,
		system=system,
		messages=[{"role": "user", "content": content}],
		output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
	)
	if settings.model.startswith(("claude-opus-5", "claude-fable-5")):
		# On a policy decline, let the API re-run the request on a fallback model.
		response = client.beta.messages.create(
			betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
		)
	else:
		response = client.messages.create(**kwargs)

	if response.stop_reason == "refusal":
		raise ValueError(f"Claude declined to process this email: {getattr(response, 'stop_details', None)}")
	if response.stop_reason == "max_tokens":
		raise ValueError("Claude response was truncated (max_tokens)")

	text = next(b.text for b in response.content if b.type == "text")
	data = json.loads(text)
	data["_skipped_attachments"] = skipped
	data["_model"] = response.model
	return _validate(data, territories)


def _validate(data: dict, territories: list[str]) -> dict:
	if data.get("territory") not in territories:
		data["territory"] = None
	if data.get("target_date"):
		try:
			datetime.date.fromisoformat(data["target_date"])
		except ValueError:
			data["target_date"] = None
	if data.get("currency"):
		data["currency"] = data["currency"].upper()[:3]
	data["confidence"] = max(0.0, min(1.0, float(data.get("confidence") or 0)))
	return data
