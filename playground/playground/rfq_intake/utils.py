# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Pure helpers with no Frappe dependency, so they can be unit-tested with plain pytest."""

import csv
import hashlib
import io
import re
import zipfile
from difflib import SequenceMatcher

FREE_MAIL_DOMAINS = {
	"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
	"yahoo.com", "yahoo.co.uk", "icloud.com", "me.com", "aol.com", "proton.me",
	"protonmail.com", "zoho.com", "gmx.com", "mail.com",
}

_FWD_PREFIX = re.compile(r"^\s*((fwd?|fw|re|aw|tr|wg)\s*:\s*)+", re.IGNORECASE)
_COMPANY_SUFFIX = re.compile(
	r"\b(pvt|private|ltd|limited|llc|inc|incorporated|corp|corporation|co|company|gmbh|plc|pte|sa|bv|llp)\b\.?",
	re.IGNORECASE,
)


def clean_subject(subject: str | None) -> str:
	"""Strip any stack of Fwd:/Re: prefixes."""
	return _FWD_PREFIX.sub("", subject or "").strip()


def email_domain(email: str | None) -> str | None:
	if not email or "@" not in email:
		return None
	return email.rsplit("@", 1)[1].strip().strip(">").lower() or None


def is_business_domain(domain: str | None, own_domains: set[str]) -> bool:
	return bool(domain) and domain not in FREE_MAIL_DOMAINS and domain not in own_domains


def normalize_company(name: str | None) -> str:
	name = _COMPANY_SUFFIX.sub(" ", (name or "").lower())
	return re.sub(r"[^a-z0-9]+", " ", name).strip()


def similarity(a: str | None, b: str | None) -> float:
	return SequenceMatcher(None, normalize_company(a), normalize_company(b)).ratio()


def best_match(needle: str | None, candidates: list[str], threshold: float) -> str | None:
	"""Return the candidate most similar to `needle`, if it clears `threshold`."""
	if not needle:
		return None
	scored = [(similarity(needle, c), c) for c in candidates]
	scored = [s for s in scored if s[0] >= threshold]
	return max(scored)[1] if scored else None


def fingerprint(subject: str | None, attachment_hashes: list[str], body_text: str | None) -> str:
	"""Stable ID for "the same RFQ", so a forward-of-a-forward doesn't create a duplicate.

	Forwarding changes the Message-ID, so we key on the cleaned subject plus attachment
	content hashes, falling back to the first 2 KB of body text when there are no attachments.
	"""
	parts = [clean_subject(subject).lower()]
	if attachment_hashes:
		parts += sorted(h for h in attachment_hashes if h)
	else:
		parts.append(re.sub(r"\s+", " ", (body_text or "")[:2000]).strip().lower())
	return hashlib.sha1("\x1f".join(parts).encode()).hexdigest()


def xlsx_to_text(content: bytes, max_rows: int = 2000) -> str:
	import openpyxl

	wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
	out = []
	for ws in wb.worksheets:
		buf = io.StringIO()
		writer = csv.writer(buf)
		for i, row in enumerate(ws.iter_rows(values_only=True)):
			if i >= max_rows:
				buf.write(f"... (truncated at {max_rows} rows)\n")
				break
			if any(v is not None for v in row):
				writer.writerow(["" if v is None else v for v in row])
		out.append(f"### Sheet: {ws.title}\n{buf.getvalue()}")
	return "\n".join(out)


def docx_to_text(content: bytes) -> str:
	"""Minimal DOCX text extraction without a python-docx dependency."""
	with zipfile.ZipFile(io.BytesIO(content)) as z:
		xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
	xml = re.sub(r"</w:p>", "\n", xml)
	xml = re.sub(r"<w:tab/>", "\t", xml)
	text = re.sub(r"<[^>]+>", "", xml)
	return re.sub(r"\n{3,}", "\n\n", text).strip()


def needs_review(data: dict, is_new_party: bool, threshold: float) -> bool:
	return (
		(data.get("confidence") or 0) < threshold
		or is_new_party
		or data.get("overall_value") is None
		or not data.get("customer_name")
	)


def summary_text(data: dict) -> str:
	lines = [
		f"Type: {data.get('doc_type')}",
		f"Title: {data.get('title')}",
		f"Customer: {data.get('customer_name')} <{data.get('customer_email') or '-'}>",
		f"Territory hint: {data.get('territory_hint') or '-'}",
		f"Target date: {data.get('target_date') or '-'}",
		f"Value: {data.get('currency') or ''} {data.get('overall_value') if data.get('overall_value') is not None else '-'}"
		f" ({data.get('value_source')})",
		f"Reference: {data.get('our_reference') or '-'}",
		f"Confidence: {data.get('confidence')}",
	]
	if data.get("notes"):
		lines.append(f"Notes: {data['notes']}")
	items = data.get("line_items") or []
	if items:
		lines.append("Line items:")
		for li in items[:50]:
			lines.append(f"  - {li.get('description')} | qty {li.get('qty')} | rate {li.get('rate')}")
	return "\n".join(lines)
