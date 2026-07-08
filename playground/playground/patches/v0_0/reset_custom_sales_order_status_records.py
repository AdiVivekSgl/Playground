# playground/playground/patches/v0_0/reset_custom_sales_order_status_records.py
"""
One-time cleanup: remove_custom_sales_order_statuses.py only reverted the
Sales Order status field's available dropdown OPTIONS - it left any Sales
Order that already had status = "Ready for Dispatch" or "Inspected" saved
on it untouched, and those two values are no longer valid options at all.

This patch finds every such record and recomputes its status back to a
standard ERPNext value via doc.set_status(update=True) - the same call
ERPNext itself uses to derive status from docstatus/delivery/billing state.

Runs once at migrate time. Each Sales Order is fixed independently; a
failure on one record is logged rather than aborting the whole patch, so it
can't block the rest from being cleaned up.
"""
import frappe


def execute():
	custom_statuses = ["Ready for Dispatch", "Inspected"]

	names = frappe.get_all(
		"Sales Order",
		filters={"status": ["in", custom_statuses]},
		pluck="name",
	)

	for name in names:
		try:
			doc = frappe.get_doc("Sales Order", name)
			doc.flags.ignore_validate_update_after_submit = True
			doc.set_status(update=True)
		except Exception:
			frappe.log_error(
				title="reset_custom_sales_order_status_records: {0}".format(name)
			)
