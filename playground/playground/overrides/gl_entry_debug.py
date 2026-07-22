# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
TEMPORARY DIAGNOSTIC - GL Entry debug for a single Delivery Note
================================================================

Purpose: capture the exact GL rows ERPNext tries to post for Delivery Note
`DC-26-27-014` (a Free Sample export DN) that fails submission with
"Row 1: Both Debit and Credit values cannot be zero", and identify which row has
debit == credit == 0 and what generated it.

Mechanism: `override_doctype_class` on "GL Entry" (see hooks.py) makes ERPNext
instantiate this subclass whenever it creates a GL Entry
(frappe.new_doc("GL Entry") inside erpnext.accounts.general_ledger.make_entry).
Our validate() logs the fully-assembled row for the TARGET voucher only, then
calls the real validation (which raises on the zero row). Everything else is
untouched - no accounting logic changed, nothing suppressed.

Why it survives the rollback: output goes to a rotating LOG FILE via
frappe.logger (flushed to disk immediately), NOT a DB document, so the failed
transaction rolling back does not lose it.

Searchable prefix: DN_GL_DEBUG_DC-26-27-014

REMOVE AFTER USE: delete the "GL Entry" line from override_doctype_class in
hooks.py and delete this file, then bench clear-cache + restart.
"""

import traceback

import frappe
from frappe.utils import flt

from erpnext.accounts.doctype.gl_entry.gl_entry import GLEntry

TARGET_VOUCHER = "DC-26-27-014"
LOG_PREFIX = "DN_GL_DEBUG_DC-26-27-014"
LOGGER_NAME = "dn_gl_debug"

# Fields dumped for every proposed GL row (requirement list + a few extras).
LOG_FIELDS = [
	"account",
	"debit",
	"credit",
	"debit_in_account_currency",
	"credit_in_account_currency",
	"account_currency",
	"against",
	"against_voucher_type",
	"against_voucher",
	"voucher_type",
	"voucher_no",
	"voucher_detail_no",  # -> Delivery Note Item row (stock line this GL row came from)
	"cost_center",
	"project",
	"party_type",
	"party",
	"remarks",
	"company",
	"posting_date",
	"is_opening",
	"is_advance",
	"transaction_currency",
	"transaction_exchange_rate",
	"finance_book",
]


def _logger():
	return frappe.logger(LOGGER_NAME, allow_site=True, file_count=50)


class DiagnosticGLEntry(GLEntry):
	def validate(self):
		if self.get("voucher_no") != TARGET_VOUCHER:
			# Untouched for every other voucher.
			return super().validate()

		self._dn_gl_debug_capture()
		try:
			return super().validate()
		except Exception as e:
			# The real validation rejected THIS row - mark it unmistakably.
			_logger().error(
				"{0} >>>>> THIS ROW RAISED: {1} | account={2} debit={3} credit={4} "
				"dr_acc_ccy={5} cr_acc_ccy={6} voucher_detail_no={7}".format(
					LOG_PREFIX, e, self.get("account"), self.get("debit"), self.get("credit"),
					self.get("debit_in_account_currency"), self.get("credit_in_account_currency"),
					self.get("voucher_detail_no"),
				)
			)
			raise

	def _dn_gl_debug_capture(self):
		logger = _logger()

		# Per-request row counter (request-scoped flags reset each submit), so the
		# numbering lines up with ERPNext's "Row N".
		idx = flt(frappe.flags.get("dn_gl_debug_row")) + 1
		frappe.flags.dn_gl_debug_row = idx

		row = {f: self.get(f) for f in LOG_FIELDS}
		is_zero = not (
			flt(self.get("debit"))
			or flt(self.get("credit"))
			or flt(self.get("debit_in_account_currency"))
			or flt(self.get("credit_in_account_currency"))
		)
		marker = "  <<<<< ZERO DEBIT & CREDIT (likely the culprit)" if is_zero else ""

		logger.error("{0} row={1}{2} {3}".format(LOG_PREFIX, int(idx), marker, frappe.as_json(row)))

		# For a zero row, also dump the call stack so the ERPNext / custom-app
		# function that generated it is identifiable.
		if is_zero:
			logger.error(
				"{0} row={1} STACK (who generated this GL row):\n{2}".format(
					LOG_PREFIX, int(idx), "".join(traceback.format_stack(limit=40))
				)
			)
