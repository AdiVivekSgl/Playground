# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Purchase Order - bulk close
===========================

Backs the "Close Purchase Orders" bulk action on the Purchase Order list view
(see playground/public/js/purchase_order_list.js).

Closes each selected Purchase Order via ERPNext's native update_status (not a raw
SQL status write), so dependent status rollups and blanket-order updates run
correctly. Each PO is processed independently in its own try/except, so one bad
document doesn't block the rest, and a per-document summary is returned.

Eligibility mirrors ERPNext's own close_or_unclose_purchase_orders:
  - submitted (docstatus == 1)
  - not already Closed or Cancelled
  - not fully completed (per_received AND per_billed both >= 100)
"""

import frappe
from frappe import _
from frappe.utils import flt

CLOSING_REASON_FIELD = "custom_closing_reason"


@frappe.whitelist()
def bulk_close_purchase_orders(names, reason=None):
	"""Close the given Purchase Orders. Returns
	{closed: [name...], skipped: [{name, reason}...], failed: {name: error}}."""
	# Gate on write permission (what ERPNext's own close action requires).
	if not frappe.has_permission("Purchase Order", "submit"):
		frappe.throw(
			_("You are not permitted to close Purchase Orders."), frappe.PermissionError
		)

	names = frappe.parse_json(names) if isinstance(names, str) else (names or [])
	names = [n for n in dict.fromkeys(names) if n]
	if not names:
		frappe.throw(_("No Purchase Orders selected."))

	reason = (reason or "").strip()
	has_reason_field = frappe.db.has_column("Purchase Order", CLOSING_REASON_FIELD)

	closed, skipped, failed = [], [], {}
	for name in names:
		try:
			if not frappe.has_permission("Purchase Order", "submit", doc=name):
				failed[name] = _("Not permitted")
				continue

			po = frappe.get_doc("Purchase Order", name)

			if po.docstatus != 1:
				skipped.append({"name": name, "reason": _("Not submitted")})
				continue
			if po.status in ("Closed", "Cancelled"):
				skipped.append({"name": name, "reason": _("Already {0}").format(po.status)})
				continue
			if flt(po.per_received) >= 100 and flt(po.per_billed) >= 100:
				skipped.append({"name": name, "reason": _("Fully received and billed")})
				continue

			# Native close path (persists + runs dependent updates).
			po.update_status("Closed")
			if hasattr(po, "update_blanket_order"):
				po.update_blanket_order()

			if reason and has_reason_field:
				frappe.db.set_value(
					"Purchase Order", name, CLOSING_REASON_FIELD, reason, update_modified=False
				)

			closed.append(name)
		except Exception as e:
			failed[name] = str(e)
			frappe.log_error(title="Bulk close Purchase Order failed: {0}".format(name))

	return {"closed": closed, "skipped": skipped, "failed": failed}
