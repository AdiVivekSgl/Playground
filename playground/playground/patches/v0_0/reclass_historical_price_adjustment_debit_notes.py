# playground/playground/patches/v0_0/reclass_historical_price_adjustment_debit_notes.py
"""
One-time GRNI reclassification for HISTORICAL Price Adjustment Debit Notes.

Existing submitted Debit Notes that were price/rate adjustments (not physical
returns) left a residual credit in Stock Received But Not Billed (GRNI), because
they were posted before the get_gl_entries() reclassification existed. Their GL
is already submitted and immutable, so we correct them with a separate Journal
Entry per document:

    Dr  Stock Received But Not Billed   <residual GRNI credit>
        Cr  Purchase Rate Adjustment    <same>

Scope: ONLY Debit Notes the user has explicitly flagged
`custom_is_price_adjustment_debit_note = 1`. Genuine Purchase Returns are left on
ERPNext's standard treatment - this patch never auto-classifies.

Idempotent: each reclass JE carries a stable marker in user_remark
("PARECLASS:<debit note>"); a document that already has one is skipped, so
re-running never double-posts. Failures on one document are logged and don't
abort the rest. A reconciliation summary is written to the Error Log.

NOTE: JE posting date is today's date (avoids submitting into a closed period);
the originating Debit Note and its own posting date are preserved in the JE
remark and the reconciliation output.
"""

import frappe
from frappe.utils import flt, nowdate

MARKER = "PARECLASS"
ADJ_ACCOUNT_COMPANY_FIELD = "custom_purchase_rate_adjustment_account"


def execute():
	if not frappe.db.has_column("Purchase Invoice", "custom_is_price_adjustment_debit_note"):
		return

	names = frappe.get_all(
		"Purchase Invoice",
		filters={"docstatus": 1, "is_return": 1, "custom_is_price_adjustment_debit_note": 1},
		pluck="name",
	)
	if not names:
		return

	recon = []
	for name in names:
		try:
			recon.append(_process(name))
		except Exception as e:
			frappe.log_error(title="Historical GRNI reclass failed: {0}".format(name))
			recon.append({"debit_note": name, "status": "ERROR: {0}".format(e)})

	_log_reconciliation(recon)


def _process(name):
	pi = frappe.get_doc("Purchase Invoice", name)
	base = {
		"debit_note": name,
		"posting_date": str(pi.posting_date),
		"supplier": pi.supplier,
		"original_pi": pi.return_against,
	}

	if _existing_reclass(name):
		base["status"] = "SKIPPED: already reclassified"
		return base

	grni = frappe.get_cached_value("Company", pi.company, "stock_received_but_not_billed")
	adj = frappe.get_cached_value("Company", pi.company, ADJ_ACCOUNT_COMPANY_FIELD)
	base["adjustment_account"] = adj
	if not grni or not adj:
		base["status"] = "SKIPPED: GRNI / adjustment account not configured"
		return base

	# Residual GRNI, split by dimensions, from this Debit Note's own GL.
	lines = frappe.db.sql(
		"""
		SELECT (credit - debit) AS net, cost_center, project
		FROM `tabGL Entry`
		WHERE voucher_type = 'Purchase Invoice' AND voucher_no = %(dn)s
			AND account = %(grni)s AND is_cancelled = 0
		""",
		{"dn": name, "grni": grni},
		as_dict=True,
	)
	total = sum(flt(l.net) for l in lines if flt(l.net) > 0)
	base["grni_amount"] = total
	if total <= 0.005:
		base["status"] = "SKIPPED: no GRNI residual"
		return base

	je = _make_reclass_je(pi, grni, adj, lines)
	base["status"] = "RECLASSIFIED"
	base["journal_entry"] = je
	return base


def _existing_reclass(dn):
	return bool(
		frappe.db.exists(
			"Journal Entry",
			{"docstatus": ["<", 2], "user_remark": ["like", "%{0}:{1}%".format(MARKER, dn)]},
		)
	)


def _make_reclass_je(pi, grni, adj, lines):
	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = pi.company
	je.posting_date = nowdate()
	je.user_remark = "{0}:{1} GRNI price-adjustment reclassification (Debit Note dated {2})".format(
		MARKER, pi.name, pi.posting_date
	)
	for l in lines:
		net = flt(l.net)
		if net <= 0.005:
			continue
		je.append("accounts", {
			"account": grni,
			"debit_in_account_currency": net,
			"cost_center": l.cost_center,
			"project": l.project,
		})
		je.append("accounts", {
			"account": adj,
			"credit_in_account_currency": net,
			"cost_center": l.cost_center,
			"project": l.project,
		})
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()
	return je.name


def _log_reconciliation(recon):
	"""Write a reconciliation table to the Error Log (title makes it easy to
	find). Columns: Debit Note, Posting Date, Supplier, Original PI, GRNI Amount,
	Adjustment Account, Status/JE."""
	header = "Debit Note | Posting Date | Supplier | Original PI | GRNI Amount | Adj Account | Status"
	lines = [header, "-" * len(header)]
	for r in recon:
		lines.append(
			"{0} | {1} | {2} | {3} | {4} | {5} | {6}{7}".format(
				r.get("debit_note"),
				r.get("posting_date", ""),
				r.get("supplier", ""),
				r.get("original_pi", ""),
				r.get("grni_amount", ""),
				r.get("adjustment_account", ""),
				r.get("status", ""),
				" ({0})".format(r["journal_entry"]) if r.get("journal_entry") else "",
			)
		)
	frappe.log_error(
		message="\n".join(lines),
		title="Price Adjustment GRNI Reclassification - Reconciliation",
	)
