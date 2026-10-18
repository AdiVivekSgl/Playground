# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""/leave-calendar?key=...&month=YYYY-MM - read-only leave calendar, no login.

Frappe never lets Guests into /app, so the desk calendar
(/app/leave-application/view/calendar/default) can't be shown without login.
This page shows the same Open/Approved Leave Applications as a month view, but
only employee name, leave type, dates, half day and status - never reasons,
balances or approvers.

It is guarded by `leave_calendar_key` in site config instead of a login: the
link (with the key) is handed out only by the WhatsApp bot to mapped numbers
(`calendar` command / the page's Leave Calendar button). No key configured ->
the page shows nothing. Rotate the key to cut off old links.
"""

import calendar
from datetime import date, timedelta
from urllib.parse import urlencode

import frappe
from frappe.utils import getdate, today

from playground.playground.whatsapp.leave import calendar_key_is_valid

no_cache = 1
sitemap = 0

STATUSES = ("Open", "Approved")
MAX_ROWS = 2000
ROUTE = "/leave-calendar"


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	key = frappe.form_dict.get("key")
	context.valid = calendar_key_is_valid(key)
	if not context.valid:
		return context

	month_start = parse_month(frappe.form_dict.get("month")) or getdate(today()).replace(day=1)
	month_end = month_start.replace(day=calendar.monthrange(month_start.year, month_start.month)[1])
	context.update(build_month(month_start, fetch_leaves(month_start, month_end), getdate(today())))
	prev_month = (month_start - timedelta(days=1)).replace(day=1)
	next_month = month_end + timedelta(days=1)
	context.prev_url = _url(key, prev_month)
	context.next_url = _url(key, next_month)
	context.today_url = _url(key, None)
	return context


def parse_month(value):
	"""'2026-10' -> date(2026, 10, 1); anything else -> None."""
	try:
		year, month = str(value or "").split("-")
		return date(int(year), int(month), 1)
	except ValueError:
		return None


def fetch_leaves(start, end):
	# Deliberately privileged (the viewer is Guest) but limited to these fields.
	return frappe.get_all(
		"Leave Application",
		filters=[
			["from_date", "<=", end],
			["to_date", ">=", start],
			["docstatus", "<", 2],
			["status", "in", STATUSES],
		],
		fields=["employee_name", "leave_type", "from_date", "to_date", "status", "half_day"],
		order_by="from_date asc, employee_name asc",
		limit_page_length=MAX_ROWS,
	)


def build_month(month_start, leaves, on):
	"""Weeks grid (Mon-Sun) and the agenda of in-month days that have leave."""
	leaves = [
		frappe._dict(row, from_date=getdate(row.from_date), to_date=getdate(row.to_date)) for row in leaves
	]
	weeks, agenda = [], []
	for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month):
		row = []
		for day in week:
			on_leave = [lv for lv in leaves if lv.from_date <= day <= lv.to_date]
			in_month = day.month == month_start.month
			row.append(
				frappe._dict(
					day=day.day,
					in_month=in_month,
					is_today=day == on,
					is_weekend=day.weekday() >= 5,
					count=len(on_leave) if in_month else 0,
				)
			)
			if in_month and on_leave:
				agenda.append(frappe._dict(label=day.strftime("%a %d %b"), is_today=day == on, leaves=on_leave))
		weeks.append(row)
	return {
		"title": month_start.strftime("%B %Y"),
		"weeks": weeks,
		"agenda": agenda,
		"people": len({lv.employee_name for lv in leaves}),
	}


def _url(key, month):
	params = {"key": key}
	if month:
		params["month"] = month.strftime("%Y-%m")
	return ROUTE + "?" + urlencode(params)
