# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Finished Kit Label Printing
===========================

Automatic label printing for finished cable-jointing kits, triggered when a Work
Order starts (status -> "In Process") rather than when manufacturing completes -
because the labels are consumed by production the moment a kit goes on the line.

Flow
----
  1. A Work Order is submitted and material transfer starts, so ERPNext moves its
     status to "In Process".
  2. `on_work_order_update` fires (Work Order.on_update). If the production item is
     flagged for finished labels and no request exists yet for this Work Order, a
     "Label Print Request" is created with Status = Pending and
     Number of Labels = Work Order Qty x Item's Labels Per Unit.
  3. A local print agent on the Windows PC next to the TSC printer polls the REST
     API for Pending requests, renders TSPL, prints, and writes back
     Printed / Failed. See print_agent/label_print_agent.py.

Design guarantees
-----------------
  - Print once per Work Order: the existence of ANY request for the Work Order is
    the dedup guard, so however many times on_update fires we create at most one.
  - Never block the Work Order: the trigger is wrapped so a labelling / DB hiccup
    is logged and swallowed - a Work Order must never fail to save because of a
    printer or label problem.

Item Master configuration (custom fields, created idempotently in after_migrate):
  - custom_enable_finished_label (Check)  : opt an item in.
  - custom_label_template (Data)          : TSPL template name, default KIT_LABEL.
  - custom_labels_per_unit (Float)        : labels per produced unit, default 1.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

PRINTER_ROLE = "Label Printer"
IN_PROCESS = "In Process"


# --------------------------------------------------------------------------- #
# Work Order trigger
# --------------------------------------------------------------------------- #
def on_work_order_update(doc, method=None):
	"""Work Order.on_update hook. Create a Pending Label Print Request the first
	time a labelled Work Order reaches "In Process".

	Deliberately swallows all errors: labelling must never abort a Work Order save
	(and on Frappe Cloud a throw inside a doc event can roll the transaction back).
	"""
	try:
		_maybe_create_label_request(doc)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Label Print Request creation failed")


def on_stock_entry_submit(doc, method=None):
	"""Stock Entry.on_submit hook.

	A Work Order only reaches "In Process" when a material-transfer Stock Entry is
	submitted against it - and ERPNext flips the Work Order's status with `db_set`
	(inside Work Order.update_status), which writes straight to the DB and does NOT
	fire Work Order.on_update. So relying on on_update alone never catches the real
	transition. We also react to the Stock Entry that caused it: reload the Work
	Order (its status is now up to date) and try to queue its label request.

	Same guarantees as the Work Order path: errors are logged and swallowed so a
	labelling hiccup can never roll back a Stock Entry, and the dedup guard in
	_maybe_create_label_request keeps it print-once even though both hooks may fire.
	"""
	try:
		work_order = doc.get("work_order")
		if not work_order:
			return
		wo = frappe.get_doc("Work Order", work_order)
		_maybe_create_label_request(wo)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Label Print Request creation failed (stock entry)")


def _maybe_create_label_request(doc):
	if doc.status != IN_PROCESS:
		return

	# Print once per Work Order - any existing request (Pending / Printed / Failed)
	# means we've already queued for this WO.
	if frappe.db.exists("Label Print Request", {"work_order": doc.name}):
		return

	item = doc.production_item
	cfg = frappe.db.get_value(
		"Item",
		item,
		["custom_enable_finished_label", "custom_label_template", "custom_labels_per_unit"],
		as_dict=True,
	) or {}

	if not cint(cfg.get("custom_enable_finished_label")):
		return

	labels_per_unit = flt(cfg.get("custom_labels_per_unit")) or 1
	number_of_labels = cint(round(flt(doc.qty) * labels_per_unit))
	if number_of_labels <= 0:
		return

	request = frappe.new_doc("Label Print Request")
	request.update({
		"work_order": doc.name,
		"production_item": item,
		"item_code": item,
		"item_name": doc.item_name or item,
		"company": doc.company,
		"batch_no": _work_order_batch(doc),
		"number_of_labels": number_of_labels,
		"label_template": (cfg.get("custom_label_template") or "KIT_LABEL").strip(),
		"status": "Pending",
	})
	request.flags.ignore_permissions = True
	request.insert()


def _work_order_batch(doc):
	"""Best-effort batch number for the label. Work Order has no native single
	batch field, so we surface one only if a custom field carries it; otherwise the
	label just omits the batch line."""
	return doc.get("batch_no") or doc.get("custom_batch_no")


# --------------------------------------------------------------------------- #
# Reprint
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def reprint(name):
	"""Reset an existing Label Print Request back to Pending so the print agent
	reprints it on its next poll. Clears the previous outcome (printed_on / error)."""
	req = frappe.get_doc("Label Print Request", name)
	req.status = "Pending"
	req.printed_on = None
	req.error_log = None
	req.save()
	frappe.msgprint(_("Reprint queued - the printer will pick it up shortly."), alert=True)
	return req.name


# --------------------------------------------------------------------------- #
# after_migrate: Item custom fields + Label Printer role
# --------------------------------------------------------------------------- #
def setup_label_printing():
	"""Create the Item Master custom fields and the Label Printer role.

	Runs as an after_migrate hook, so it is wrapped defensively: a failure here must
	never abort migrate (on Frappe Cloud a failed migrate rolls the whole DB back).
	If setup fails it is logged and the migrate still completes; it can be re-run on
	the next migrate."""
	try:
		_create_label_printer_role()
		_create_item_label_fields()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "setup_label_printing failed")


def _create_label_printer_role():
	if frappe.db.exists("Role", PRINTER_ROLE):
		return
	role = frappe.new_doc("Role")
	role.role_name = PRINTER_ROLE
	role.desk_access = 1
	role.flags.ignore_permissions = True
	role.insert()


def _create_item_label_fields():
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	fields = [
		{
			"fieldname": "custom_finished_label_section",
			"label": "Finished Label",
			"fieldtype": "Section Break",
			"insert_after": "stock_uom",
			"collapsible": 1,
		},
		{
			"fieldname": "custom_enable_finished_label",
			"label": "Enable Finished Label",
			"fieldtype": "Check",
			"insert_after": "custom_finished_label_section",
			"description": "Print finished-kit labels automatically when a Work Order for this item starts.",
		},
		{
			"fieldname": "custom_label_template",
			"label": "Label Template",
			"fieldtype": "Data",
			"insert_after": "custom_enable_finished_label",
			"default": "KIT_LABEL",
			"depends_on": "custom_enable_finished_label",
			"description": "TSPL template the print agent renders (default KIT_LABEL).",
		},
		{
			"fieldname": "custom_labels_per_unit",
			"label": "Labels Per Unit",
			"fieldtype": "Float",
			"insert_after": "custom_label_template",
			"default": "1",
			"depends_on": "custom_enable_finished_label",
			"description": "Number of labels per produced unit. Number of Labels = Work Order Qty x this.",
		},
	]
	create_custom_fields({"Item": fields}, ignore_validate=True)
