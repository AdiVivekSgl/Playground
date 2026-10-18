# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Leave request / leave calendar tests.

	bench --site <site> run-tests --module playground.playground.whatsapp.tests.test_leave

frappe.set_user / get_doc / db / conf are faked, so nothing is read or written
and the real session is never switched.
"""

import unittest
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from playground.playground.whatsapp import leave, router
from playground.playground.whatsapp.phone import Sender
from playground.www import leave_calendar

USER = "ravi@frontec.in"
MAPPED = Sender(phone="919812345678", user=USER)
UNMAPPED = Sender(phone="919812345678", user=None)
TODAY = "2026-10-18"
EMPLOYEE = frappe._dict(name="HR-EMP-00001", employee_name="Ravi Kumar", company="Frontec Technologies")

# Exactly what the FTPL-Attendance page puts in the WhatsApp message.
PAGE_MESSAGE = (
	"🌴 FRONTEC LEAVE REQUEST\n"
	"Leave Type: Casual Leave\n"
	"From: 2026-10-20 (Tue)\n"
	"To: 2026-10-22 (Thu)"
)


class FakeLeaveDoc(frappe._dict):
	def __init__(self, fake, data):
		super().__init__(data)
		self._fake = fake

	def insert(self, ignore_permissions=False):
		self._fake.inserts.append((dict(self), self._fake.session.user, ignore_permissions))
		if self._fake.insert_error:
			raise self._fake.insert_error
		# What HRMS fills in on a real insert.
		self.name = "HR-LAP-2026-00012"
		self.total_leave_days = 3
		self.leave_approver_name = "Priya Shah" if self.leave_approver else None
		return self


class FakeFrappe:
	def __init__(self, employee=EMPLOYEE, insert_error=None, conf=None):
		self.session = SimpleNamespace(user="Guest")
		self.employee = employee
		self.insert_error = insert_error
		self.conf = frappe._dict(conf or {})
		self.set_user_calls = []
		self.inserts = []
		self.savepoints = []
		self.rollbacks = []
		self.cleared = 0

	def set_user(self, user):
		self.set_user_calls.append(user)
		self.session.user = user

	def get_doc(self, data):
		return FakeLeaveDoc(self, data)

	def get_value(self, doctype, filters, fieldname, as_dict=False):
		assert doctype == "Employee" and filters == {"user_id": USER, "status": "Active"}, (doctype, filters)
		return self.employee

	def savepoint(self, name):
		self.savepoints.append(name)

	def rollback(self, save_point=None):
		self.rollbacks.append(save_point)

	def clear_messages(self):
		self.cleared += 1


class LeaveTestCase(unittest.TestCase):
	@contextmanager
	def fake(self, **kwargs):
		fake = FakeFrappe(**kwargs)
		with patch.object(frappe, "session", fake.session), \
			patch.object(frappe, "set_user", fake.set_user), \
			patch.object(frappe, "get_doc", fake.get_doc), \
			patch.object(frappe, "conf", fake.conf), \
			patch.object(frappe, "clear_messages", fake.clear_messages, create=True), \
			patch.object(frappe.db, "get_value", fake.get_value), \
			patch.object(frappe.db, "savepoint", fake.savepoint, create=True), \
			patch.object(frappe.db, "rollback", fake.rollback, create=True), \
			patch.object(leave, "today", lambda: TODAY), \
			patch.object(leave, "get_url", lambda path: "https://livefrontec.frappe.cloud" + path), \
			patch.object(leave, "_leave_approver", lambda employee: "priya@frontec.in"):
			yield fake


class TestParsing(unittest.TestCase):
	def test_markers(self):
		self.assertTrue(leave.is_leave_request(PAGE_MESSAGE))
		self.assertFalse(leave.is_calendar_request(PAGE_MESSAGE))
		self.assertTrue(leave.is_calendar_request("📅 FRONTEC LEAVE CALENDAR\nPlease send me the leave calendar link."))
		self.assertFalse(leave.is_leave_request("stock XYZ-123"))

	def test_dates(self):
		self.assertEqual(leave.parse_dates(PAGE_MESSAGE), (date(2026, 10, 20), date(2026, 10, 22)))
		self.assertIsNone(leave.parse_dates("🌴 FRONTEC LEAVE REQUEST\nFrom: 2026-10-20"))
		self.assertIsNone(leave.parse_dates(PAGE_MESSAGE.replace("2026-10-22", "2026-02-30")))
		self.assertIsNone(leave.parse_dates(PAGE_MESSAGE.replace("2026-10-22", "22/10/2026")))

	def test_range_rules(self):
		on = date(2026, 10, 18)
		self.assertIsNone(leave.check_range(date(2026, 10, 20), date(2026, 10, 20), on))
		self.assertIn("before the start", leave.check_range(date(2026, 10, 22), date(2026, 10, 20), on))
		self.assertIn("at most 60 days", leave.check_range(date(2026, 10, 20), date(2026, 12, 31), on))
		self.assertIn("more than 30 days ago", leave.check_range(date(2026, 9, 1), date(2026, 9, 2), on))
		self.assertIn("a year ahead", leave.check_range(date(2027, 12, 1), date(2027, 12, 2), on))


class TestRequestLeave(LeaveTestCase):
	def test_page_message_creates_leave_application_as_the_user(self):
		with self.fake() as fake:
			reply = leave.request_leave(PAGE_MESSAGE, MAPPED)

		self.assertEqual(len(fake.inserts), 1)
		row, inserted_as, ignore_permissions = fake.inserts[0]
		self.assertEqual(inserted_as, USER)
		self.assertFalse(ignore_permissions)  # HRMS permissions apply
		self.assertEqual(fake.set_user_calls, [USER, "Guest"])
		self.assertEqual(fake.session.user, "Guest")

		self.assertEqual(row["doctype"], "Leave Application")
		self.assertEqual(row["employee"], "HR-EMP-00001")  # from the mapping, never the message
		self.assertEqual(row["company"], "Frontec Technologies")
		self.assertEqual(row["leave_type"], "Casual Leave")
		self.assertEqual((row["from_date"], row["to_date"]), (date(2026, 10, 20), date(2026, 10, 22)))
		self.assertEqual(row["half_day"], 0)
		self.assertEqual(row["status"], "Open")
		self.assertEqual(row["leave_approver"], "priya@frontec.in")
		self.assertEqual(fake.rollbacks, [])

		self.assertEqual(
			reply,
			"✅ Leave requested - *Ravi Kumar*\n"
			"🌴 Casual Leave: 20-10-2026 → 22-10-2026 (3 days)\n"
			"📄 HR-LAP-2026-00012 - status *Open*, waiting for Priya Shah",
		)

	def test_leave_type_in_message_is_ignored(self):
		with self.fake() as fake:
			leave.request_leave(PAGE_MESSAGE.replace("Casual Leave", "Privilege Leave"), MAPPED)
		self.assertEqual(fake.inserts[0][0]["leave_type"], "Casual Leave")

	def test_unmapped_sender(self):
		with self.fake() as fake:
			reply = leave.request_leave(PAGE_MESSAGE, UNMAPPED)
		self.assertIn("not linked", reply)
		self.assertEqual((fake.inserts, fake.set_user_calls), ([], []))

	def test_user_without_employee(self):
		with self.fake(employee=None) as fake:
			reply = leave.request_leave(PAGE_MESSAGE, MAPPED)
		self.assertEqual(reply, leave.NO_EMPLOYEE_REPLY)
		self.assertEqual(fake.inserts, [])

	def test_bad_or_out_of_range_dates(self):
		with self.fake() as fake:
			self.assertEqual(leave.request_leave("🌴 FRONTEC LEAVE REQUEST\nFrom: soon", MAPPED), leave.BAD_DATES_REPLY)
			reply = leave.request_leave(PAGE_MESSAGE.replace("2026-10-22", "2026-10-19"), MAPPED)
		self.assertIn("before the start", reply)
		self.assertEqual(fake.inserts, [])

	def test_hrms_validation_error_is_returned_and_rolled_back(self):
		error = frappe.ValidationError("Employee <b>Ravi Kumar</b> has already applied for <b>Casual Leave</b> between 20-10-2026 and 22-10-2026")
		with self.fake(insert_error=error) as fake:
			reply = leave.request_leave(PAGE_MESSAGE, MAPPED)
		self.assertEqual(
			reply,
			"Sorry, the leave request was not accepted:\n"
			"Employee Ravi Kumar has already applied for Casual Leave between 20-10-2026 and 22-10-2026",
		)
		self.assertEqual(fake.rollbacks, ["wa_leave_request"])
		self.assertEqual(fake.cleared, 1)
		self.assertEqual(fake.session.user, "Guest")

	def test_permission_error(self):
		with self.fake(insert_error=frappe.PermissionError("no")) as fake:
			reply = leave.request_leave(PAGE_MESSAGE, MAPPED)
		self.assertEqual(reply, leave.PERMISSION_DENIED_REPLY)
		self.assertEqual(fake.rollbacks, ["wa_leave_request"])
		self.assertEqual(fake.session.user, "Guest")

	def test_session_restored_on_crash(self):
		with self.fake(insert_error=RuntimeError("db down")) as fake:
			with self.assertRaises(RuntimeError):
				leave.request_leave(PAGE_MESSAGE, MAPPED)
		self.assertEqual(fake.set_user_calls, [USER, "Guest"])


class TestCalendarAndMenu(LeaveTestCase):
	def test_calendar_link_for_mapped_sender(self):
		with self.fake(conf={"leave_calendar_key": "s3cr3t"}):
			reply = leave.calendar_link("", MAPPED)
		self.assertIn("https://livefrontec.frappe.cloud/leave-calendar?key=s3cr3t", reply)
		self.assertIn(leave.DEFAULT_PAGE_URL + "#k=s3cr3t", reply)

	def test_page_link_carries_key_only_for_mapped_senders(self):
		with self.fake(conf={"leave_calendar_key": "s3cr3t", "ftpl_attendance_url": "https://example.test/ftpl/#old"}):
			self.assertEqual(leave.page_url(MAPPED), "https://example.test/ftpl/#k=s3cr3t")
			self.assertIn("https://example.test/ftpl/#k=s3cr3t", leave.menu("", MAPPED))
			self.assertNotIn("s3cr3t", leave.menu("", UNMAPPED))
		with self.fake():  # no key configured
			self.assertEqual(leave.page_url(MAPPED), leave.DEFAULT_PAGE_URL)

	def test_calendar_link_refused_or_off(self):
		with self.fake(conf={"leave_calendar_key": "s3cr3t"}):
			self.assertNotIn("s3cr3t", leave.calendar_link("", UNMAPPED))
		with self.fake():
			self.assertEqual(leave.calendar_link("", MAPPED), leave.CALENDAR_OFF_REPLY)

	def test_calendar_key_check(self):
		with self.fake(conf={"leave_calendar_key": "s3cr3t"}):
			self.assertTrue(leave.calendar_key_is_valid("s3cr3t"))
			self.assertFalse(leave.calendar_key_is_valid("wrong"))
			self.assertFalse(leave.calendar_key_is_valid(None))
		with self.fake():  # not configured -> nothing is valid
			self.assertFalse(leave.calendar_key_is_valid(""))

	def test_menu(self):
		with self.fake():
			self.assertIn(leave.DEFAULT_PAGE_URL, leave.menu("", UNMAPPED))
		with self.fake(conf={"ftpl_attendance_url": "https://example.test/ftpl/"}):
			self.assertIn("https://example.test/ftpl/", leave.menu("", MAPPED))

	def test_routing(self):
		with self.fake(conf={"leave_calendar_key": "k"}) as fake:
			self.assertEqual(router.route(PAGE_MESSAGE, MAPPED).intent, "leave_request")
			self.assertEqual(router.route("📅 FRONTEC LEAVE CALENDAR\nPlease send me the leave calendar link.", MAPPED).intent, "calendar")
			self.assertEqual(router.route("calendar", MAPPED).intent, "calendar")
			self.assertIn(leave.DEFAULT_PAGE_URL, router.route("menu", MAPPED).reply)
			self.assertIn(leave.DEFAULT_PAGE_URL, router.route("Leave", MAPPED).reply)
		self.assertEqual(len(fake.inserts), 1)


class TestCalendarPage(unittest.TestCase):
	def test_parse_month(self):
		self.assertEqual(leave_calendar.parse_month("2026-10"), date(2026, 10, 1))
		for bad in (None, "", "2026", "2026-13", "x-y", "2026-10-01"):
			self.assertIsNone(leave_calendar.parse_month(bad), bad)

	def test_build_month(self):
		leaves = [
			frappe._dict(employee_name="Ravi Kumar", leave_type="Casual Leave", from_date="2026-09-29", to_date="2026-10-02", status="Approved", half_day=0),
			frappe._dict(employee_name="Asha Rao", leave_type="Casual Leave", from_date="2026-10-02", to_date="2026-10-02", status="Open", half_day=1),
		]
		month = leave_calendar.build_month(date(2026, 10, 1), leaves, date(2026, 10, 2))

		self.assertEqual(month["title"], "October 2026")
		self.assertEqual(month["people"], 2)
		first_week = month["weeks"][0]  # Mon 28 Sep .. Sun 4 Oct
		self.assertEqual([d.day for d in first_week], [28, 29, 30, 1, 2, 3, 4])
		self.assertEqual([d.count for d in first_week], [0, 0, 0, 1, 2, 0, 0])  # out-of-month days show no count
		self.assertTrue(first_week[4].is_today)
		self.assertTrue(first_week[5].is_weekend)
		self.assertEqual([a.label for a in month["agenda"]], ["Thu 01 Oct", "Fri 02 Oct"])
		self.assertEqual([lv.employee_name for lv in month["agenda"][1].leaves], ["Ravi Kumar", "Asha Rao"])


if __name__ == "__main__":
	unittest.main()
