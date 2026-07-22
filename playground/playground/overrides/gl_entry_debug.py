# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
TEMPORARY DIAGNOSTIC - GL Entry debug for a single Delivery Note
================================================================

Captures the GL rows ERPNext tries to post for Delivery Note `DC-26-27-014`
(a Free Sample export DN) that fails submission with "Row 1: Both Debit and
Credit values cannot be zero", to identify the zero debit/credit row and what
generated it.

Mechanism: `override_doctype_class` on "GL Entry" (see hooks.py) makes ERPNext
instantiate this subclass whenever it creates a GL Entry (inside
erpnext.accounts.general_ledger.make_entry). Our validate() logs the row for the
TARGET voucher only, then calls the real validation (which raises on the zero
row). No accounting logic is changed and nothing is suppressed.

Output goes to TWO places:
  1. Server log file  sites/<site>/logs/dn_gl_debug.log  (fallback).
  2. Desk -> Error Log, title "DN GL DEBUG - DC-26-27-014 - ZERO GL ROW".

Rollback safety (the important bit): the Delivery Note submit transaction ROLLS
BACK after the validation throw, so a normal frappe.log_error() (inserted in the
same transaction) would be rolled back and lost. Instead we ENQUEUE a background
job (`frappe.enqueue(..., enqueue_after_commit=False)`): the job is pushed to
Redis IMMEDIATELY - independent of the DB transaction - so it survives the
rollback, and the worker creates the Error Log in its OWN committed transaction.
This does NOT commit any Delivery Note / Stock Ledger / GL Entry data; only the
diagnostic Error Log is written, by a separate worker.

Searchable prefix: DN_GL_DEBUG_DC-26-27-014
Error Log title:   DN GL DEBUG - DC-26-27-014 - ZERO GL ROW

REMOVE AFTER USE: delete the "GL Entry" line from override_doctype_class in
hooks.py and delete this file, then bench clear-cache + restart.
"""

import traceback

import frappe
from frappe.utils import flt

from erpnext.accounts.doctype.gl_entry.gl_entry import GLEntry

TARGET_VOUCHER = "DC-26-27-014"
LOG_PREFIX = "DN_GL_DEBUG_DC-26-27-014"
ERROR_LOG_TITLE_ZERO = "DN GL DEBUG - DC-26-27-014 - ZERO GL ROW"
ERROR_LOG_TITLE_RAISED = "DN GL DEBUG - DC-26-27-014 - VALIDATION RAISED"
LOGGER_NAME = "dn_gl_debug"
WORKER_METHOD = "playground.playground.overrides.gl_entry_debug.write_diagnostic_error_log"

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

		row, is_zero, idx = self._dn_gl_debug_capture()
		try:
			return super().validate()
		except Exception as e:
			# The real validation rejected THIS row - pin it down in an Error Log.
			_logger().error(
				"{0} >>>>> THIS ROW RAISED: {1} | account={2} debit={3} credit={4} "
				"voucher_detail_no={5}".format(
					LOG_PREFIX, e, self.get("account"), self.get("debit"),
					self.get("credit"), self.get("voucher_detail_no"),
				)
			)
			_enqueue_error_log(
				ERROR_LOG_TITLE_RAISED,
				heading="VALIDATION ABOUT TO FAIL ON THIS ROW: {0}".format(e),
				idx=idx,
				is_zero=is_zero,
				error=str(e),
				row=row,
				stack="".join(traceback.format_stack(limit=40)),
			)
			raise

	def _dn_gl_debug_capture(self):
		logger = _logger()

		# Per-request row counter (flags reset each request), so numbering lines
		# up with ERPNext's "Row N".
		idx = int(flt(frappe.flags.get("dn_gl_debug_row")) + 1)
		frappe.flags.dn_gl_debug_row = idx

		row = {f: self.get(f) for f in LOG_FIELDS}
		is_zero = not (
			flt(self.get("debit"))
			or flt(self.get("credit"))
			or flt(self.get("debit_in_account_currency"))
			or flt(self.get("credit_in_account_currency"))
		)

		# Accumulate the whole map (this request) for context in the Error Log.
		captured = frappe.flags.setdefault("dn_gl_debug_all", [])
		captured.append(dict(idx=idx, is_zero=is_zero, **row))

		marker = "  <<<<< ZERO DEBIT & CREDIT (likely the culprit)" if is_zero else ""
		logger.error("{0} row={1}{2} {3}".format(LOG_PREFIX, idx, marker, frappe.as_json(row)))

		if is_zero:
			stack = "".join(traceback.format_stack(limit=40))
			logger.error("{0} row={1} STACK (who generated this GL row):\n{2}".format(LOG_PREFIX, idx, stack))
			_enqueue_error_log(
				ERROR_LOG_TITLE_ZERO,
				heading="ZERO DEBIT & CREDIT GL ROW (the row about to fail validation)",
				idx=idx,
				is_zero=True,
				error="debit == credit == 0 (and account-currency debit/credit == 0)",
				row=row,
				stack=stack,
			)

		return row, is_zero, idx


def _enqueue_error_log(title, heading, idx, is_zero, error, row, stack):
	"""Enqueue a background job to write the Error Log. enqueue_after_commit=False
	pushes to Redis immediately - independent of the DB transaction - so it
	survives the Delivery Note submit rollback. The job (a separate worker) writes
	ONLY the Error Log; no Delivery Note / SLE / GL data is committed by us."""
	message = _build_message(heading, idx, is_zero, error, row, stack, frappe.flags.get("dn_gl_debug_all"))
	try:
		frappe.enqueue(
			WORKER_METHOD,
			enqueue_after_commit=False,
			queue="short",
			title=title,
			message=message,
		)
	except Exception:
		_logger().error("{0} FAILED to enqueue Error Log job (see server log for row details)".format(LOG_PREFIX))


def _build_message(heading, idx, is_zero, error, row, stack, all_rows):
	return "\n".join([
		heading,
		"Prefix: {0}".format(LOG_PREFIX),
		"Voucher: Delivery Note {0}".format(TARGET_VOUCHER),
		"Row index: {0}    zero_debit_credit={1}".format(idx, is_zero),
		"Validation error: {0}".format(error),
		"",
		"=== PROBLEM GL ROW (all fields) ===",
		frappe.as_json(row, indent=1),
		"",
		"=== ALL GL ROWS CAPTURED THIS SUBMIT (validation order) ===",
		frappe.as_json(all_rows or [], indent=1),
		"",
		"=== PYTHON CALL STACK (which function generated / validated this row) ===",
		stack,
	])


def write_diagnostic_error_log(title, message):
	"""Runs in a background worker (its own transaction, which commits normally),
	so the Error Log persists even though the Delivery Note submit transaction
	rolled back. Visible at Desk -> Error Log."""
	frappe.log_error(title=title, message=message)
