# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
BOM Update Tool - replace a component item across BOMs
======================================================

Backs the "Replace Component Item" button added to ERPNext's native BOM Update
Tool form (see playground/public/js/bom_update_tool.js).

ERPNext's built-in "Replace BOM" swaps one *sub-assembly BOM reference* for
another. This adds the missing sibling: swap one *component item* (a BOM Item
row) for a different item across every BOM that uses it - and it works for any
item, whether the replacement is a purchased raw material or a manufactured
sub-assembly (whose own default BOM is picked up automatically).

Flow (mirrors the JS):
  1. find_affected_boms(old_item)  -> every active/draft BOM containing the item,
     so the user can preview and tick which ones to update.
  2. replace_component_item(...)   -> swaps the item on the selected BOMs.

Each BOM is processed inside its own DB savepoint, so one bad document doesn't
roll back or block the rest, and a per-document summary is returned.

Submitted BOMs are edited in place (same BOM name/version) the way ERPNext's own
BOM Update Tool mutates submitted BOMs: the row's item identity + cost fields are
refreshed from the replacement item, costs and the exploded-items table are
recomputed, then persisted with the update-after-submit guard relaxed. Parent
BOMs that *use* an edited BOM are NOT re-costed here - run the BOM Update Tool's
native "Update Cost" afterwards to cascade, exactly as after any BOM edit.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


@frappe.whitelist()
def find_affected_boms(old_item):
	"""Every non-cancelled BOM that has ``old_item`` as a component row.

	Returns a list of dicts: {bom, bom_item, item_name, occurrences, is_active,
	is_default, docstatus}, active/default BOMs first."""
	old_item = (old_item or "").strip()
	if not old_item:
		frappe.throw(_("Select the item to be replaced."))

	rows = frappe.db.sql(
		"""
		SELECT
			bi.parent            AS bom,
			COUNT(*)             AS occurrences,
			b.item               AS bom_item,
			b.item_name          AS item_name,
			b.is_active          AS is_active,
			b.is_default         AS is_default,
			b.docstatus          AS docstatus
		FROM `tabBOM Item` bi
		INNER JOIN `tabBOM` b ON b.name = bi.parent
		WHERE bi.item_code = %(item)s
		  AND bi.parenttype = 'BOM'
		  AND b.docstatus < 2
		GROUP BY bi.parent
		ORDER BY b.is_active DESC, b.is_default DESC, bi.parent
		""",
		{"item": old_item},
		as_dict=True,
	)
	return rows


@frappe.whitelist()
def replace_component_item(old_item, new_item, boms):
	"""Replace component ``old_item`` with ``new_item`` on each BOM in ``boms``.

	Returns {updated: [name...], skipped: [{name, reason}...], failed: {name: error}}."""
	if not frappe.has_permission("BOM", "write"):
		frappe.throw(_("You are not permitted to edit BOMs."), frappe.PermissionError)

	old_item = (old_item or "").strip()
	new_item = (new_item or "").strip()
	if not old_item or not new_item:
		frappe.throw(_("Both the current item and the replacement item are required."))
	if old_item == new_item:
		frappe.throw(_("The replacement item is the same as the current item."))
	if not frappe.db.exists("Item", new_item):
		frappe.throw(_("Replacement item {0} does not exist.").format(new_item))
	if cint(frappe.db.get_value("Item", new_item, "disabled")):
		frappe.throw(_("Replacement item {0} is disabled.").format(new_item))

	boms = frappe.parse_json(boms) if isinstance(boms, str) else (boms or [])
	boms = [b for b in dict.fromkeys(boms) if b]
	if not boms:
		frappe.throw(_("Select at least one BOM to update."))

	updated, skipped, failed = [], [], {}
	for bom_name in boms:
		savepoint = "replace_component"
		try:
			frappe.db.savepoint(savepoint)
			reason = _replace_in_bom(bom_name, old_item, new_item)
			if reason:
				frappe.db.rollback(save_point=savepoint)
				skipped.append({"name": bom_name, "reason": reason})
			else:
				updated.append(bom_name)
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			failed[bom_name] = str(e)
			frappe.log_error(title="Replace component item failed: {0}".format(bom_name))

	return {"updated": updated, "skipped": skipped, "failed": failed}


def _replace_in_bom(bom_name, old_item, new_item):
	"""Swap ``old_item`` for ``new_item`` on one BOM, in place.

	Returns None on success, or a human-readable string when the BOM was skipped
	(the caller rolls the savepoint back for skips). Raises on hard errors."""
	if not frappe.db.exists("BOM", bom_name):
		return _("BOM no longer exists")

	bom = frappe.get_doc("BOM", bom_name)
	if bom.docstatus == 2:
		return _("BOM is cancelled")

	# A BOM can never contain its own manufactured item as a component.
	if new_item == bom.item:
		return _("{0} is the item this BOM produces").format(new_item)

	matched = [d for d in bom.items if d.item_code == old_item]
	if not matched:
		return _("{0} is not a component of this BOM").format(old_item)

	for row in matched:
		# Pull the replacement item's details exactly the way the BOM form does
		# when you type an item code into a row: item name, description, UOM,
		# conversion factor, rate (per the BOM's own costing settings) and, if the
		# item is manufactured, its default BOM - so a purchase item lands as a
		# raw material and a manufactured item lands as a sub-assembly.
		detail = bom.get_bom_material_detail(
			{
				"item_code": new_item,
				"bom_no": "",
				"qty": flt(row.qty),
				"stock_qty": flt(row.stock_qty),
				"include_item_in_manufacturing": cint(
					row.get("include_item_in_manufacturing")
				),
			}
		)

		new_bom_no = detail.get("bom_no") or None
		# Guard against a circular reference: the replacement sub-assembly's tree
		# must not already contain the item this BOM produces.
		if new_bom_no and frappe.db.exists(
			"BOM Explosion Item", {"parent": new_bom_no, "item_code": bom.item}
		):
			return _(
				"Replacing with {0} would create a circular BOM (its sub-assembly {1} already contains {2})"
			).format(new_item, new_bom_no, bom.item)

		conversion_factor = flt(detail.get("conversion_factor")) or 1.0
		stock_qty = flt(row.qty) * conversion_factor
		rate = flt(detail.get("rate"))

		row.item_code = new_item
		row.item_name = detail.get("item_name")
		row.description = detail.get("description")
		row.image = detail.get("image")
		row.stock_uom = detail.get("stock_uom")
		row.uom = detail.get("uom") or detail.get("stock_uom")
		row.conversion_factor = conversion_factor
		row.bom_no = new_bom_no
		row.qty = flt(row.qty)
		row.stock_qty = stock_qty
		row.rate = rate
		row.base_rate = flt(detail.get("base_rate")) or (rate * (flt(bom.conversion_rate) or 1.0))
		row.amount = stock_qty * rate
		row.base_amount = row.amount * (flt(bom.conversion_rate) or 1.0)

	# Recompute the BOM the way ERPNext does after editing items, then persist.
	# calculate_cost() re-derives every row's amount + the BOM totals; the
	# exploded-items table is rebuilt from the new component set.
	bom.calculate_cost()
	bom.update_exploded_items(save=False)

	if bom.docstatus == 1:
		# Same pattern ERPNext's own BOM Update Tool uses to mutate submitted
		# BOMs: relax the update-after-submit guard so the item-row changes
		# (which are not allow-on-submit fields) can be written. save() persists
		# the parent, item rows and rebuilt exploded items through the ORM.
		bom.flags.ignore_validate_update_after_submit = True

	bom.save(ignore_permissions=True)
	return None
