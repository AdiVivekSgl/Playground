# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Leave requests and the leave calendar link, from the FTPL-Attendance page.

The page's "Request Leave" calendar popup opens WhatsApp with:

	🌴 FRONTEC LEAVE REQUEST
	Leave Type: Casual Leave
	From: 2026-10-20 (Tue)
	To: 2026-10-22 (Thu)

and "Leave Calendar" opens WhatsApp with "📅 FRONTEC LEAVE CALENDAR".

The Employee is never taken from the message: it is the active Employee whose
User ID is the sender's mapped ERPNext user (WhatsApp User), exactly like
Remote Attendance. The leave type is always Casual Leave. The Leave
Application is created as that user, through the normal permission-checked
insert, so HRMS' own rules (balance, overlap, holidays, leave approver,
notifications) all apply.
"""

import hmac
import re
from datetime import date, timedelta
from urllib.parse import urlencode

import frappe
from frappe.utils import get_url, getdate, strip_html_tags, today

from playground.playground.whatsapp.user_context import as_user

LEAVE_MARKER = "FRONTEC LEAVE REQUEST"
CALENDAR_MARKER = "FRONTEC LEAVE CALENDAR"
LEAVE_TYPE = "Casual Leave"
# Request window: not more than this far back / ahead, nor longer than this.
MAX_DAYS_BACK = 30
MAX_DAYS_AHEAD = 365
MAX_SPAN_DAYS = 60
ERROR_TEXT_LIMIT = 300

# Site config. leave_calendar_key unset -> the calendar page and link are off.
CALENDAR_KEY_CONF = "leave_calendar_key"
PAGE_URL_CONF = "ftpl_attendance_url"
DEFAULT_PAGE_URL = "https://adiviveksgl.github.io/FTPL-Attendance/"
CALENDAR_ROUTE = "/leave-calendar"

_LEAVE_RE = re.compile(re.escape(LEAVE_MARKER), re.IGNORECASE)
_CALENDAR_RE = re.compile(re.escape(CALENDAR_MARKER), re.IGNORECASE)
_FROM_RE = re.compile(r"\bfrom\s*:\s*(?P<d>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_TO_RE = re.compile(r"\bto\s*:\s*(?P<d>\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)

NOT_LINKED_REPLY = (
	"Sorry, your WhatsApp number is not linked to an ERPNext user, so I can't {what}. "
	"Please ask your ERPNext administrator to add it under *WhatsApp User*."
)
NO_EMPLOYEE_REPLY = (
	"Sorry, your ERPNext user is not linked to an active Employee, so no leave was requested. "
	"Please ask HR to set your user as *User ID* on your Employee record."
)
BAD_DATES_REPLY = (
	"Sorry, I couldn't read the leave dates, so no leave was requested. "
	"Please open the Frontec Attendance page, pick the dates in the calendar and send the message without editing it."
)
DATE_RANGE_REPLY = "Sorry, no leave was requested: {reason}"
PERMISSION_DENIED_REPLY = "Sorry, your ERPNext user doesn't have permission to apply for leave. Please contact HR."
REJECTED_REPLY = "Sorry, the leave request was not accepted:\n{error}"
REQUESTED_REPLY = (
	"✅ Leave requested - *{name}*\n"
	"🌴 {leave_type}: {from_date} → {to_date} ({days} day{s})\n"
	"📄 {application} - status *{status}*{approver}"
)
CALENDAR_REPLY = "📅 Frontec leave calendar (read-only, no login needed):\n{url}\n\nPlease don't share this link outside Frontec."
CALENDAR_OFF_REPLY = "Sorry, the leave calendar is not set up yet. Please ask your ERPNext administrator."
MENU_REPLY = (
	"👋 Frontec Attendance & Leave\n"
	"Open this page to mark attendance, request leave or check the leave calendar:\n{url}"
)


def is_leave_request(message):
	return bool(_LEAVE_RE.search(message or ""))


def is_calendar_request(message):
	return bool(_CALENDAR_RE.search(message or ""))


def parse_dates(message):
	"""Return (from_date, to_date) as dates, or None when either is missing or not a real date."""
	text = message or ""
	start, end = _FROM_RE.search(text), _TO_RE.search(text)
	if not (start and end):
		return None
	try:
		return date.fromisoformat(start.group("d")), date.fromisoformat(end.group("d"))
	except ValueError:
		return None


def check_range(from_date, to_date, on=None):
	"""Return None when the range is acceptable, else the reason it isn't."""
	on = on or getdate(today())
	if to_date < from_date:
		return "the end date is before the start date."
	if (to_date - from_date).days + 1 > MAX_SPAN_DAYS:
		return f"a single request can cover at most {MAX_SPAN_DAYS} days."
	if from_date < on - timedelta(days=MAX_DAYS_BACK):
		return f"the start date is more than {MAX_DAYS_BACK} days ago."
	if to_date > on + timedelta(days=MAX_DAYS_AHEAD):
		return "the end date is more than a year ahead."
	return None


def request_leave(message, sender):
	if not sender or not sender.user:
		return NOT_LINKED_REPLY.format(what="request leave")
	dates = parse_dates(message)
	if dates is None:
		return BAD_DATES_REPLY
	from_date, to_date = dates
	reason = check_range(from_date, to_date)
	if reason:
		return DATE_RANGE_REPLY.format(reason=reason)

	employee = _employee_for(sender.user)
	if not employee:
		return NO_EMPLOYEE_REPLY

	with as_user(sender.user):
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee.name,
				"company": employee.company,
				"leave_type": LEAVE_TYPE,
				"from_date": from_date,
				"to_date": to_date,
				"half_day": 0,
				"posting_date": today(),
				"status": "Open",
				"leave_approver": _leave_approver(employee.name),
				"description": f"Requested on WhatsApp from {sender.phone}",
			}
		)
		# Savepoint: a rejected insert must leave nothing behind, because the
		# request itself still commits (the WhatsApp Query Log update).
		frappe.db.savepoint("wa_leave_request")
		try:
			doc.insert()  # permission-checked, as the sender's user
		except frappe.PermissionError:
			frappe.db.rollback(save_point="wa_leave_request")
			return PERMISSION_DENIED_REPLY
		except frappe.ValidationError as e:
			# HRMS' own reasons: insufficient balance, overlapping leave, holidays...
			frappe.db.rollback(save_point="wa_leave_request")
			return REJECTED_REPLY.format(error=_error_text(e))
		finally:
			# frappe.throw queues desk popups; WhatsApp replies don't use them.
			frappe.clear_messages()

	return format_requested_reply(doc, employee)


def format_requested_reply(doc, employee):
	# total_leave_days is HRMS' count (holidays excluded); fall back to calendar days.
	days = doc.get("total_leave_days")
	if days is None:
		days = (getdate(doc.to_date) - getdate(doc.from_date)).days + 1
	days_text = f"{float(days):g}"
	approver = doc.get("leave_approver_name") or doc.get("leave_approver")
	return REQUESTED_REPLY.format(
		name=employee.employee_name or employee.name,
		leave_type=doc.leave_type,
		from_date=getdate(doc.from_date).strftime("%d-%m-%Y"),
		to_date=getdate(doc.to_date).strftime("%d-%m-%Y"),
		days=days_text,
		s="" if days_text == "1" else "s",
		application=doc.name,
		status=doc.get("status") or "Open",
		approver=f", waiting for {approver}" if approver else "",
	)


def calendar_link(args, sender):
	"""`calendar` command / the page's Leave Calendar button: the secret calendar link."""
	if not sender or not sender.user:
		return NOT_LINKED_REPLY.format(what="share the leave calendar")
	key = frappe.conf.get(CALENDAR_KEY_CONF)
	if not key:
		return CALENDAR_OFF_REPLY
	return CALENDAR_REPLY.format(url=get_url(CALENDAR_ROUTE) + "?" + urlencode({"key": key}))


def menu(args, sender):
	"""`menu` / `leave`: the link to the FTPL-Attendance page."""
	return MENU_REPLY.format(url=frappe.conf.get(PAGE_URL_CONF) or DEFAULT_PAGE_URL)


def calendar_key_is_valid(provided):
	expected = frappe.conf.get(CALENDAR_KEY_CONF)
	if not expected or not provided:
		return False
	return hmac.compare_digest(str(provided).encode(), str(expected).encode())


def _employee_for(user):
	# Server-side lookup of the sender's own Employee record.
	return frappe.db.get_value(
		"Employee", {"user_id": user, "status": "Active"}, ["name", "employee_name", "company"], as_dict=True
	)


def _leave_approver(employee):
	try:
		from hrms.hr.doctype.leave_application.leave_application import get_leave_approver
	except ImportError:
		return None
	return get_leave_approver(employee) or None


def _error_text(exc):
	text = " ".join(strip_html_tags(str(exc) or "").split())
	if not text:
		text = "please check the dates and your leave balance."
	return text if len(text) <= ERROR_TEXT_LIMIT else text[: ERROR_TEXT_LIMIT - 1] + "…"
