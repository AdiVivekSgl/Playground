# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

import io
import unittest
import zipfile

from playground.playground.rfq_intake import utils


class TestUtils(unittest.TestCase):
	def test_clean_subject_strips_forward_stack(self):
		self.assertEqual(utils.clean_subject("Fwd: RE: FW: RFQ 1234 - Pumps"), "RFQ 1234 - Pumps")
		self.assertEqual(utils.clean_subject(None), "")

	def test_email_domain(self):
		self.assertEqual(utils.email_domain("Jane <jane@Acme.COM>"), "acme.com")
		self.assertIsNone(utils.email_domain("not-an-email"))

	def test_business_domain_excludes_free_and_own(self):
		own = {"ourco.com"}
		self.assertTrue(utils.is_business_domain("acme.com", own))
		self.assertFalse(utils.is_business_domain("gmail.com", own))
		self.assertFalse(utils.is_business_domain("ourco.com", own))

	def test_company_match_ignores_suffixes(self):
		self.assertEqual(
			utils.best_match("ACME Industries Pvt. Ltd.", ["Acme Industries", "Acme Foods"], 0.85),
			"Acme Industries",
		)
		self.assertIsNone(utils.best_match("Globex", ["Acme Industries"], 0.85))

	def test_fingerprint_stable_across_forwards(self):
		a = utils.fingerprint("Fwd: RFQ 99", ["h2", "h1"], "body")
		b = utils.fingerprint("FW: Fwd: RFQ 99", ["h1", "h2"], "different body")
		self.assertEqual(a, b)
		self.assertNotEqual(a, utils.fingerprint("RFQ 99", ["h3"], ""))

	def test_fingerprint_uses_body_without_attachments(self):
		self.assertNotEqual(
			utils.fingerprint("RFQ", [], "need 10 pumps"),
			utils.fingerprint("RFQ", [], "need 20 valves"),
		)

	def test_docx_to_text(self):
		buf = io.BytesIO()
		with zipfile.ZipFile(buf, "w") as z:
			z.writestr(
				"word/document.xml",
				"<w:document><w:body><w:p><w:r><w:t>Line one</w:t></w:r></w:p>"
				"<w:p><w:r><w:t>Qty</w:t><w:tab/><w:t>10</w:t></w:r></w:p></w:body></w:document>",
			)
		self.assertEqual(utils.docx_to_text(buf.getvalue()), "Line one\nQty\t10")

	def test_xlsx_to_text(self):
		import openpyxl

		wb = openpyxl.Workbook()
		ws = wb.active
		ws.title = "BOM"
		ws.append(["Item", "Qty"])
		ws.append(["Pump", 4])
		buf = io.BytesIO()
		wb.save(buf)
		text = utils.xlsx_to_text(buf.getvalue())
		self.assertIn("### Sheet: BOM", text)
		self.assertIn("Pump,4", text)

	def test_needs_review(self):
		good = {"confidence": 0.9, "overall_value": 100, "customer_name": "Acme"}
		self.assertFalse(utils.needs_review(good, False, 0.7))
		self.assertTrue(utils.needs_review(good, True, 0.7))
		self.assertTrue(utils.needs_review({**good, "confidence": 0.5}, False, 0.7))
		self.assertTrue(utils.needs_review({**good, "overall_value": None}, False, 0.7))
