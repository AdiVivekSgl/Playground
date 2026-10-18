# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Consolidation steps for one old_item -> keep_item pair
======================================================

1. move_stock      - one Repack Stock Entry per warehouse, posted now: consume all
                     of old_item, produce the same qty of keep_item. ERPNext values
                     the finished row from the consumed value, so stock value and
                     GL are unchanged and nothing is reposted into closed periods.
2. copy_masters    - Item Price, reorder levels, supplier / customer item codes,
                     Product Bundle and Pricing Rule rows (only what keep_item lacks),
                     and Item Alternatives moved from old_item to keep_item.
3. replace_in_boms - reuses bom_component_replace._replace_in_bom on every active
                     or draft BOM using old_item.
4. disable_item    - disabled = 1 plus a "Replaced by" comment. History stays on
                     old_item; the item code is not renamed or merged.
"""

import frappe
from frappe import _
from frappe.utils import flt

from playground.playground.bom_component_replace import _replace_in_bom

# Item child tables copied to keep_item, keyed by the fields that identify a row.
ITEM_CHILD_TABLES = (
	("reorder_levels", ("warehouse", "material_request_type")),
	("supplier_items", ("supplier",)),
	("customer_items", ("customer_name",)),
)
ITEM_PRICE_KEY = ("price_list", "uom", "customer", "supplier")
SKIP_CHILD_FIELDS = {"name", "parent", "parentfield", "parenttype", "idx", "owner", "creation", "modified", "modified_by", "docstatus"}


def move_stock(old_item, keep_item, company, stock_rows, stock_uom, reference):
	"""Repack every warehouse's stock of old_item into keep_item. Returns SE names."""
	names = []
	for row in stock_rows:
		qty = flt(row.actual_qty)
		if qty <= 0:
			continue
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Repack"
		se.purpose = "Repack"
		se.company = company
		se.remarks = _("Item Consolidation {0}: {1} replaced by {2}").format(reference, old_item, keep_item)
		common = {"qty": qty, "transfer_qty": qty, "uom": stock_uom, "stock_uom": stock_uom, "conversion_factor": 1}
		se.append("items", dict(common, item_code=old_item, s_warehouse=row.warehouse))
		se.append(
			"items",
			dict(
				common,
				item_code=keep_item,
				t_warehouse=row.warehouse,
				is_finished_item=1,
				# Zero-value stock stays zero-value instead of failing the rate check.
				allow_zero_valuation_rate=1 if flt(row.stock_value) == 0 else 0,
			),
		)
		se.insert()
		se.submit()
		names.append(se.name)
	return names


def copy_masters(old_item, keep_item):
	"""Copy pricing / planning masters keep_item does not already have and move its
	Item Alternatives. Returns (summary list, skipped-record notes)."""
	done = []

	prices = _copy_item_prices(old_item, keep_item)
	if prices:
		done.append(_("{0} Item Price(s)").format(prices))

	keep = frappe.get_doc("Item", keep_item)
	old = frappe.get_doc("Item", old_item)
	changed = False
	# Item Alternative refuses records on items without this flag, so carry it over
	# before the alternatives are moved below.
	if old.allow_alternative_item and not keep.allow_alternative_item:
		keep.allow_alternative_item = 1
		changed = True
		done.append(_("Allow Alternative Item enabled"))
	for table, key in ITEM_CHILD_TABLES:
		existing = {_key(r, key) for r in keep.get(table)}
		added = 0
		for row in old.get(table):
			if _key(row, key) in existing:
				continue
			keep.append(table, {f: v for f, v in row.as_dict().items() if f not in SKIP_CHILD_FIELDS})
			existing.add(_key(row, key))
			added += 1
		if added:
			changed = True
			done.append(_("{0} {1} row(s)").format(added, keep.meta.get_label(table)))
	if changed:
		keep.save()

	bundles = _replace_in_parents("Product Bundle", "Product Bundle Item", old_item, keep_item, replace=True)
	if bundles:
		done.append(_("Product Bundle(s) {0}").format(", ".join(bundles)))

	rules = _replace_in_parents("Pricing Rule", "Pricing Rule Item Code", old_item, keep_item, replace=False)
	if rules:
		done.append(_("Pricing Rule(s) {0}").format(", ".join(rules)))

	moved, skipped = transfer_item_alternatives(old_item, keep_item)
	if moved:
		done.append(_("Item Alternative(s) {0}").format(", ".join(moved)))
	return done, skipped


def transfer_item_alternatives(old_item, keep_item):
	"""Re-point every Item Alternative that involves old_item - as the item or as
	the alternative - to keep_item, in place (version history is kept).

	A record is skipped, and left on old_item, when it would point keep_item at
	itself, when keep_item already has the same alternative, or when ERPNext's own
	Item Alternative validation refuses it (e.g. stock / batch / serial flags
	differ). Returns (moved labels, skipped reasons)."""
	moved, skipped = [], []
	keep_name = frappe.db.get_value("Item", keep_item, "item_name")
	names = frappe.get_all(
		"Item Alternative",
		or_filters={"item_code": old_item, "alternative_item_code": old_item},
		pluck="name",
	)
	for name in names:
		alt = frappe.get_doc("Item Alternative", name)
		if alt.item_code == old_item:
			side, name_field = "item_code", "item_name"
			item_code, alternative = keep_item, alt.alternative_item_code
		else:
			side, name_field = "alternative_item_code", "alternative_item_name"
			item_code, alternative = alt.item_code, keep_item
		label = "{0} → {1}".format(item_code, alternative)

		if item_code == alternative:
			skipped.append(_("Item Alternative {0} not moved: it would make {1} its own alternative").format(name, keep_item))
			continue
		if _alternative_exists(item_code, alternative, exclude=name):
			skipped.append(_("Item Alternative {0} not moved: {1} already exists").format(name, label))
			continue

		savepoint = "item_consolidation_alternative"
		frappe.db.savepoint(savepoint)
		try:
			alt.set(side, keep_item)
			alt.set(name_field, keep_name)
			alt.save()
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			frappe.local.message_log = []
			skipped.append(
				_("Item Alternative {0} ({1}) not moved: {2}").format(name, label, frappe.utils.strip_html(str(e)))
			)
		else:
			moved.append(label)
	return moved, skipped


def _alternative_exists(item_code, alternative, exclude=None):
	"""Same pair already recorded, or covered by a two-way record in reverse."""
	base = {"name": ["!=", exclude]} if exclude else {}
	return bool(
		frappe.db.exists("Item Alternative", dict(base, item_code=item_code, alternative_item_code=alternative))
		or frappe.db.exists(
			"Item Alternative", dict(base, item_code=alternative, alternative_item_code=item_code, two_way=1)
		)
	)


def replace_in_boms(old_item, keep_item, boms):
	"""Swap old_item for keep_item in ``boms`` (rows from checks._active_boms).

	Each BOM runs in its own savepoint so one bad BOM doesn't fail the pair.
	Returns (updated, skipped) where skipped is a list of "BOM: reason"."""
	updated, skipped = [], []
	for row in boms:
		savepoint = "item_consolidation_bom"
		frappe.db.savepoint(savepoint)
		try:
			reason = _replace_in_bom(row.bom, old_item, keep_item)
		except Exception as e:
			frappe.db.rollback(save_point=savepoint)
			skipped.append("{0}: {1}".format(row.bom, e))
			continue
		if reason:
			frappe.db.rollback(save_point=savepoint)
			skipped.append("{0}: {1}".format(row.bom, reason))
		else:
			updated.append(row.bom)
	return updated, skipped


def disable_item(old_item, keep_item, reference, open_documents=None):
	"""``open_documents`` (from checks.get_open_documents) is set when the user chose to
	disable despite them; they are listed in the comment so the override is on record."""
	item = frappe.get_doc("Item", old_item)
	item.db_set("disabled", 1)
	text = _("Disabled by Item Consolidation {0}: replaced by {1}.").format(reference, keep_item)
	if open_documents:
		text += " " + _("Disabled despite open documents: {0}").format(
			"; ".join("{0}: {1}".format(d["doctype"], ", ".join(d["names"])) for d in open_documents)
		)
	item.add_comment("Comment", text)


def _copy_item_prices(old_item, keep_item):
	existing = {
		_key(p, ITEM_PRICE_KEY)
		for p in frappe.get_all("Item Price", filters={"item_code": keep_item}, fields=list(ITEM_PRICE_KEY))
	}
	copied = 0
	for p in frappe.get_all(
		"Item Price",
		# Batch-specific prices belong to old_item's batches; leave them behind.
		filters={"item_code": old_item, "batch_no": ["is", "not set"]},
		fields=["name", *ITEM_PRICE_KEY],
	):
		if _key(p, ITEM_PRICE_KEY) in existing:
			continue
		price = frappe.copy_doc(frappe.get_doc("Item Price", p.name))
		price.item_code = keep_item
		price.item_name = price.item_description = None
		price.insert()
		existing.add(_key(p, ITEM_PRICE_KEY))
		copied += 1
	return copied


def _replace_in_parents(parent_doctype, child_doctype, old_item, keep_item, replace):
	"""Point ``child_doctype`` rows at keep_item. ``replace`` swaps the row; otherwise
	keep_item is added beside it. Parents that already list keep_item are skipped."""
	parents = frappe.get_all(
		child_doctype,
		filters={"item_code": old_item, "parenttype": parent_doctype},
		pluck="parent",
		distinct=True,
	)
	changed = []
	for name in parents:
		doc = frappe.get_doc(parent_doctype, name)
		rows = doc.get(_child_field(doc, child_doctype))
		if any(r.item_code == keep_item for r in rows):
			continue
		for r in [r for r in rows if r.item_code == old_item]:
			if replace:
				r.item_code = keep_item
				if r.meta.has_field("description"):
					r.description = frappe.db.get_value("Item", keep_item, "description")
			else:
				doc.append(r.parentfield, {"item_code": keep_item, "uom": r.get("uom")})
		doc.save()
		changed.append(name)
	return changed


def _child_field(doc, child_doctype):
	return next(df.fieldname for df in doc.meta.get_table_fields() if df.options == child_doctype)


def _key(row, fields):
	return tuple((row.get(f) or "") for f in fields)
