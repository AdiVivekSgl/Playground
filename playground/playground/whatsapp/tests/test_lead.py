# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""`lead` command tests: parsing, mapping required, inserted as the mapped user.

	bench --site <site> run-tests --module playground.playground.whatsapp.tests.test_lead

frappe.set_user / get_doc / get_meta / db are faked, so nothing is written and
the real session is never switched.
"""

import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from playground.playground.whatsapp import lead, router
from playground.playground.whatsapp.phone import Sender

USER = "ravi@frontec.in"
MAPPED = Sender(phone="919812345678", user=USER)
UNMAPPED = Sender(phone="919812345678", user=None)

EXAMPLE = "Aditya Saigal; +919811546751; adisag@gmail.com; aditya is ceo and MD of frontec, thgey make cable joints."


class FakeLeadDoc(frappe._dict):
	def __init__(self, fake, data):
		super().__init__(data)
		self._fake = fake

	def append(self, table, row):
		self.setdefault(table, []).append(row)

	def insert(self, ignore_permissions=False):
		self._fake.inserts.append((dict(self), self._fake.session.user, ignore_permissions))
		if self._fake.insert_error:
			raise self._fake.insert_error
		# What ERPNext fills in on a real insert.
		self.name = "CRM-LEAD-2026-00042"
		self.lead_name = " ".join(filter(None, (self.first_name, self.last_name)))
		return self

	def add_comment(self, comment_type, text):
		self._fake.comments.append((comment_type, text, self._fake.session.user))


class FakeFrappe:
	def __init__(self, insert_error=None, has_notes_table=True):
		self.session = SimpleNamespace(user="Guest")
		self.insert_error = insert_error
		self.has_notes_table = has_notes_table
		self.set_user_calls = []
		self.inserts = []
		self.comments = []
		self.savepoints = []
		self.rollbacks = []

	def set_user(self, user):
		self.set_user_calls.append(user)
		self.session.user = user

	def get_doc(self, data):
		return FakeLeadDoc(self, data)

	def get_meta(self, doctype):
		assert doctype == "Lead", doctype
		return SimpleNamespace(has_field=lambda f: f == "notes" and self.has_notes_table)

	def savepoint(self, name):
		self.savepoints.append(name)

	def rollback(self, save_point=None):
		self.rollbacks.append(save_point)


class LeadTestCase(unittest.TestCase):
	@contextmanager
	def fake(self, **kwargs):
		fake = FakeFrappe(**kwargs)
		with patch.object(frappe, "session", fake.session), \
			patch.object(frappe, "set_user", fake.set_user), \
			patch.object(frappe, "get_doc", fake.get_doc), \
			patch.object(frappe, "get_meta", fake.get_meta, create=True), \
			patch.object(frappe, "clear_messages", lambda: None, create=True), \
			patch.object(frappe.db, "savepoint", fake.savepoint, create=True), \
			patch.object(frappe.db, "rollback", fake.rollback, create=True), \
			patch.object(lead, "now_datetime", lambda: "2026-10-21 10:00:00"), \
			patch.object(lead, "get_url_to_form", lambda dt, name: f"https://livefrontec.frappe.cloud/app/lead/{name}"):
			yield fake


class TestParseLead(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(frappe, "conf", {})
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_example_message(self):
		fields, note = lead.parse_lead(EXAMPLE)
		self.assertEqual(
			fields,
			{"first_name": "Aditya", "last_name": "Saigal", "mobile_no": "+919811546751", "email_id": "adisag@gmail.com"},
		)
		self.assertEqual(note, "aditya is ceo and MD of frontec, thgey make cable joints.")

	def test_parts_in_any_order_and_new_lines(self):
		fields, note = lead.parse_lead("Priya\nPriya@Example.COM\n098 1154 6751\ncompany: Acme Cables\ntitle: Buyer\nMet at expo: wants quote")
		self.assertEqual(
			fields,
			{
				"first_name": "Priya",
				"email_id": "priya@example.com",
				"mobile_no": "+919811546751",
				"company_name": "Acme Cables",
				"job_title": "Buyer",
			},
		)
		self.assertEqual(note, "Met at expo: wants quote")  # unknown label -> note

	def test_name_only(self):
		self.assertEqual(lead.parse_lead("  Aditya   Kumar Saigal ;; "), ({"first_name": "Aditya", "last_name": "Kumar Saigal"}, ""))

	def test_rejected(self):
		for args, reason in (
			(" ; \n ;", "no name"),
			("; adisag@gmail.com", "must be the person's name"),
			("adisag@gmail.com; Aditya", "must be the person's name"),
			("+919811546751", "must be the person's name"),
			("Aditya; a@b.com; c@d.com", "more than one e-mail"),
			("Aditya; email: not-an-email", "not a valid e-mail"),
			("Aditya; phone: 12", "not a valid phone number"),
			("A" * 200, "longer than"),
			("Aditya; " + "x" * 3000, "notes are longer"),
		):
			with self.assertRaises(ValueError, msg=args) as ctx:
				lead.parse_lead(args)
			self.assertIn(reason, str(ctx.exception), args)

	def test_registered_in_router(self):
		self.assertIs(router.COMMANDS["lead"], lead.create_lead)
		self.assertEqual(router.parse_command("Lead " + EXAMPLE), ("lead", EXAMPLE))


class TestCreateLead(LeadTestCase):
	def test_creates_lead_as_the_mapped_user(self):
		with self.fake() as fake:
			reply = lead.create_lead(EXAMPLE, MAPPED)
		self.assertEqual(fake.set_user_calls, [USER, "Guest"])
		self.assertEqual(fake.session.user, "Guest")
		self.assertEqual(len(fake.inserts), 1)
		data, inserted_as, ignore_permissions = fake.inserts[0]
		self.assertEqual(inserted_as, USER)
		self.assertFalse(ignore_permissions)
		self.assertEqual(data["doctype"], "Lead")
		self.assertEqual(data["email_id"], "adisag@gmail.com")
		self.assertEqual(data["mobile_no"], "+919811546751")
		self.assertEqual(
			data["notes"],
			[{"note": "aditya is ceo and MD of frontec, thgey make cable joints.", "added_by": USER, "added_on": "2026-10-21 10:00:00"}],
		)
		self.assertEqual(fake.rollbacks, [])
		self.assertEqual(
			reply,
			"✅ Lead created - *CRM-LEAD-2026-00042*\n"
			"👤 Aditya Saigal\n"
			"📞 +919811546751\n"
			"✉️ adisag@gmail.com\n"
			"📝 aditya is ceo and MD of frontec, thgey make cable joints.\n"
			"https://livefrontec.frappe.cloud/app/lead/CRM-LEAD-2026-00042",
		)

	def test_note_is_html_escaped(self):
		with self.fake() as fake:
			lead.create_lead("Aditya; <b>vip</b>", MAPPED)
		self.assertEqual(fake.inserts[0][0]["notes"][0]["note"], "&lt;b&gt;vip&lt;/b&gt;")

	def test_note_falls_back_to_comment_without_notes_table(self):
		with self.fake(has_notes_table=False) as fake:
			lead.create_lead("Aditya; met at expo", MAPPED)
		self.assertNotIn("notes", fake.inserts[0][0])
		self.assertEqual(fake.comments, [("Comment", "met at expo", USER)])

	def test_unmapped_sender_is_refused(self):
		with self.fake() as fake:
			reply = lead.create_lead(EXAMPLE, UNMAPPED)
		self.assertEqual(reply, lead.NOT_LINKED_REPLY)
		self.assertEqual(fake.set_user_calls, [])
		self.assertEqual(fake.inserts, [])

	def test_no_details_shows_usage(self):
		with self.fake() as fake:
			self.assertEqual(lead.create_lead("  ", MAPPED), lead.USAGE_REPLY)
		self.assertEqual(fake.inserts, [])

	def test_bad_details_are_explained_without_insert(self):
		with self.fake() as fake:
			reply = lead.create_lead("Aditya; a@b.com; c@d.com", MAPPED)
		self.assertIn("more than one e-mail", reply)
		self.assertEqual(fake.inserts, [])

	def test_permission_denied_rolls_back(self):
		with self.fake(insert_error=frappe.PermissionError("no create")) as fake:
			reply = lead.create_lead(EXAMPLE, MAPPED)
		self.assertEqual(reply, lead.PERMISSION_DENIED_REPLY)
		self.assertEqual(fake.rollbacks, ["wa_lead"])
		self.assertEqual(fake.session.user, "Guest")

	def test_erpnext_validation_error_is_reported(self):
		error = frappe.ValidationError("Email Address must be unique, it is already used in <b>CRM-LEAD-2026-00001</b>")
		with self.fake(insert_error=error) as fake:
			reply = lead.create_lead(EXAMPLE, MAPPED)
		self.assertEqual(
			reply, "Sorry, the lead was not created:\nEmail Address must be unique, it is already used in CRM-LEAD-2026-00001"
		)
		self.assertEqual(fake.rollbacks, ["wa_lead"])

	def test_session_restored_when_insert_crashes(self):
		with self.fake(insert_error=RuntimeError("db down")) as fake:
			with self.assertRaises(RuntimeError):
				lead.create_lead(EXAMPLE, MAPPED)
		self.assertEqual(fake.set_user_calls, [USER, "Guest"])
		self.assertEqual(fake.session.user, "Guest")


if __name__ == "__main__":
	unittest.main()
