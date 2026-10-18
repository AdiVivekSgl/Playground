# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""Remote attendance tests: message parsing, mapping required, row written as the mapped user.

	bench --site <site> run-tests --module playground.playground.whatsapp.tests.test_attendance

frappe.set_user / get_doc / db.get_value are faked, so nothing is written and
the real session is never switched.
"""

import json
import unittest
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from playground.playground.whatsapp import attendance, router
from playground.playground.whatsapp.phone import Sender

USER = "ravi@frontec.in"
MAPPED = Sender(phone="919812345678", user=USER)
UNMAPPED = Sender(phone="919812345678", user=None)
NOW = datetime(2026, 10, 18, 9, 12, 30)

# Exactly what the FTPL-Attendance page puts in the WhatsApp message.
PAGE_MESSAGE = (
	"📍 FRONTEC ATTENDANCE\n"
	"Location: https://www.google.com/maps?q=17.385044,78.486671\n"
	"Accuracy: 12m\n"
	"Ref: FA-MGW3X1K2-9F3A21C7"
)
REF = "FA-MGW3X1K2-9F3A21C7"


class FakeDoc(frappe._dict):
	def __init__(self, fake, data):
		super().__init__(data)
		self._fake = fake

	def insert(self, ignore_permissions=False):
		self._fake.inserts.append((dict(self), self._fake.session.user, ignore_permissions))
		# What fetch_from fills in on a real insert.
		self.full_name = "Ravi Kumar"
		self.employee_name = "Ravi Kumar (EMP)" if self.employee else None
		if self._fake.insert_error:
			raise self._fake.insert_error
		return self


class FakeFrappe:
	def __init__(self, employee="HR-EMP-00001", insert_error=None, used_refs=()):
		self.session = SimpleNamespace(user="Guest")
		self.used_refs = set(used_refs)
		self.ref_checks = []
		self.employee = employee
		self.insert_error = insert_error
		self.set_user_calls = []
		self.inserts = []
		self.employee_lookups = []

	def set_user(self, user):
		self.set_user_calls.append(user)
		self.session.user = user

	def get_doc(self, data):
		return FakeDoc(self, data)

	def exists(self, doctype, filters):
		assert doctype == "Remote Attendance", doctype
		self.ref_checks.append(filters["message_ref"])
		return "RA-2026-00001" if filters["message_ref"] in self.used_refs else None

	def get_value(self, doctype, filters, fieldname):
		assert doctype == "Employee", doctype
		self.employee_lookups.append(filters)
		return self.employee


class AttendanceTestCase(unittest.TestCase):
	@contextmanager
	def fake(self, **kwargs):
		fake = FakeFrappe(**kwargs)
		with patch.object(frappe, "session", fake.session), \
			patch.object(frappe, "set_user", fake.set_user), \
			patch.object(frappe, "get_doc", fake.get_doc), \
			patch.object(frappe.db, "get_value", fake.get_value), \
			patch.object(frappe.db, "exists", fake.exists), \
			patch.object(attendance, "now_datetime", lambda: NOW):
			yield fake


class TestParsing(unittest.TestCase):
	def test_marker(self):
		self.assertTrue(attendance.is_attendance_message(PAGE_MESSAGE))
		self.assertTrue(attendance.is_attendance_message("frontec attendance maps?q=1,2"))
		self.assertFalse(attendance.is_attendance_message("stock XYZ-123"))
		self.assertFalse(attendance.is_attendance_message(None))

	def test_page_message(self):
		self.assertEqual(attendance.parse_location(PAGE_MESSAGE), (17.385044, 78.486671, 12))

	def test_negative_coords_and_missing_accuracy(self):
		text = "📍 FRONTEC ATTENDANCE\nLocation: https://www.google.com/maps?q=-33.868820, -151.209290"
		self.assertEqual(attendance.parse_location(text), (-33.86882, -151.20929, None))

	def test_invalid_coords(self):
		for text in (
			"📍 FRONTEC ATTENDANCE",
			"📍 FRONTEC ATTENDANCE\nLocation: https://www.google.com/maps?q=95.0,10.0",
			"📍 FRONTEC ATTENDANCE\nLocation: https://www.google.com/maps?q=10.0,190.5",
			"📍 FRONTEC ATTENDANCE\nLocation: somewhere near the office",
		):
			self.assertIsNone(attendance.parse_location(text), text)


class TestRecordAttendance(AttendanceTestCase):
	def test_page_message_creates_row_as_mapped_user(self):
		with self.fake() as fake:
			reply = attendance.record_attendance(PAGE_MESSAGE, MAPPED)

		self.assertEqual(len(fake.inserts), 1)
		row, inserted_as, ignore_permissions = fake.inserts[0]
		self.assertEqual(inserted_as, USER)
		self.assertTrue(ignore_permissions)
		self.assertEqual(fake.set_user_calls, [USER, "Guest"])
		self.assertEqual(fake.session.user, "Guest")
		self.assertEqual(fake.employee_lookups, [{"user_id": USER, "status": "Active"}])

		self.assertEqual(row["doctype"], "Remote Attendance")
		self.assertEqual(row["user"], USER)
		self.assertEqual(row["employee"], "HR-EMP-00001")
		self.assertEqual(row["phone"], "919812345678")
		self.assertEqual(row["timestamp"], NOW)
		self.assertEqual((row["latitude"], row["longitude"], row["accuracy"]), (17.385044, 78.486671, 12))
		self.assertEqual(row["low_accuracy"], 0)
		self.assertEqual(row["map_link"], "https://www.google.com/maps?q=17.385044,78.486671")
		self.assertEqual(json.loads(row["location"])["features"][0]["geometry"]["coordinates"], [78.486671, 17.385044])
		self.assertEqual(row["source_message"], PAGE_MESSAGE)

		self.assertEqual(
			reply,
			"✅ Attendance recorded for *Ravi Kumar (EMP)*\n"
			"🕒 18-10-2026 09:12\n"
			"📍 17.385044, 78.486671 (±12 m)",
		)

	def test_user_without_employee(self):
		with self.fake(employee=None) as fake:
			reply = attendance.record_attendance(PAGE_MESSAGE, MAPPED)
		self.assertIsNone(fake.inserts[0][0]["employee"])
		self.assertIn("*Ravi Kumar*", reply)

	def test_low_or_missing_accuracy_is_flagged(self):
		for text in (PAGE_MESSAGE.replace("12m", "250m"), PAGE_MESSAGE.replace("Accuracy: 12m\n", "")):
			with self.fake() as fake:
				reply = attendance.record_attendance(text, MAPPED)
			self.assertEqual(fake.inserts[0][0]["low_accuracy"], 1, text)
			self.assertTrue(reply.endswith(attendance.LOW_ACCURACY_NOTE), reply)

	def test_unmapped_sender_records_nothing(self):
		with self.fake() as fake:
			reply = attendance.record_attendance(PAGE_MESSAGE, UNMAPPED)
		self.assertEqual(reply, attendance.NOT_LINKED_REPLY)
		self.assertEqual((fake.inserts, fake.set_user_calls), ([], []))

	def test_no_location_records_nothing(self):
		with self.fake() as fake:
			reply = attendance.record_attendance("📍 FRONTEC ATTENDANCE\nLocation: home", MAPPED)
		self.assertEqual(reply, attendance.NO_LOCATION_REPLY)
		self.assertEqual((fake.inserts, fake.set_user_calls), ([], []))

	def test_ref_is_stored(self):
		with self.fake() as fake:
			attendance.record_attendance(PAGE_MESSAGE.replace(REF, REF.lower()), MAPPED)
		self.assertEqual(fake.ref_checks, [REF])
		self.assertEqual(fake.inserts[0][0]["message_ref"], REF)

	def test_forwarded_or_resent_message_is_rejected(self):
		forwarded = "Forwarded\n" + PAGE_MESSAGE
		for sender in (MAPPED, Sender(phone="919800000000", user="someone@frontec.in")):
			with self.fake(used_refs={REF}) as fake:
				reply = attendance.record_attendance(forwarded, sender)
			self.assertEqual(reply, attendance.ALREADY_USED_REPLY.format(ref=REF))
			self.assertEqual((fake.inserts, fake.set_user_calls), ([], []))

	def test_message_without_ref_is_rejected(self):
		for text in (
			PAGE_MESSAGE.rsplit("\n", 1)[0],  # old page / typed by hand
			PAGE_MESSAGE.replace(REF, "FA-123"),  # malformed
			PAGE_MESSAGE.replace(REF, "FA-MGW3X1K2-9F3A21C7ZZ"),  # longer than a real Ref
		):
			with self.fake() as fake:
				reply = attendance.record_attendance(text, MAPPED)
			self.assertEqual(reply, attendance.NO_REF_REPLY, text)
			self.assertEqual(fake.inserts, [])

	def test_simultaneous_duplicate_loses_the_unique_index(self):
		with self.fake(insert_error=frappe.UniqueValidationError("dup")) as fake:
			reply = attendance.record_attendance(PAGE_MESSAGE, MAPPED)
		self.assertEqual(reply, attendance.ALREADY_USED_REPLY.format(ref=REF))
		self.assertEqual(fake.session.user, "Guest")

	def test_session_restored_when_insert_fails(self):
		with self.fake(insert_error=RuntimeError("db down")) as fake:
			with self.assertRaises(RuntimeError):
				attendance.record_attendance(PAGE_MESSAGE, MAPPED)
		self.assertEqual(fake.set_user_calls, [USER, "Guest"])
		self.assertEqual(fake.session.user, "Guest")


class TestRouting(AttendanceTestCase):
	def test_page_message_is_routed_to_attendance(self):
		with self.fake() as fake:
			result = router.route(PAGE_MESSAGE, MAPPED)
		self.assertEqual(result.intent, "attendance")
		self.assertTrue(result.reply.startswith("✅ Attendance recorded"), result.reply)
		self.assertEqual(len(fake.inserts), 1)

	def test_other_messages_are_not_attendance(self):
		with self.fake() as fake:
			self.assertEqual(router.route("hello", MAPPED).intent, "hello")
		self.assertEqual(fake.inserts, [])


if __name__ == "__main__":
	unittest.main()
