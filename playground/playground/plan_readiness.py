# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
Production Plan Readiness
=========================

Pre-flight check run before FGSRM / Weekly Planning Snapshot build a Production
Plan, and again when the unified workbook is downloaded (its "Action Items" sheet).

It walks each finished good's BOM tree the way the nested plan chain will -
frontec's create_full_hierarchy turns every Manufacture-type component into a
child-plan row and recurses into its BOM - and reports what would stop or degrade
the plan, each issue with deep links to fix it:

  Blockers (the item can't be planned):
    no_bom         - no active, submitted default BOM (a draft / inactive /
                     non-default BOM is pointed out so the fix is one click)
    item_disabled  - item is disabled
    end_of_life    - item is past its end-of-life date
    no_uom         - item has no stock UOM
    item_missing   - item code not found
  Warnings (the plan builds, the workbook is less useful):
    no_rate            - no valuation / last purchase rate (costs show as 0)
    no_supplier        - purchased item with no default supplier
    bom_disabled_item  - a BOM uses a disabled item

Each issue carries the BOM path to it (FG > SA > item) and the finished goods it
affects, so the caller can either cut off just the broken branches (frontec skips
them) or leave the affected finished goods out entirely.
"""

from urllib.parse import quote, urlencode

import frappe
from frappe import _
from frappe.utils import flt, formatdate, getdate, nowdate

BLOCKER = "blocker"
WARNING = "warning"
MAX_DEPTH = 10  # frontec's MAX_LEVELS for the nested chain

_ITEM_FIELDS = [
	"name",
	"item_name",
	"stock_uom",
	"disabled",
	"end_of_life",
	"default_bom",
	"variant_of",
	"default_material_request_type",
	"is_stock_item",
	"valuation_rate",
	"last_purchase_rate",
]


def check_readiness(fg_items):
	"""Readiness of the finished goods `fg_items` (the plan's root assembly items).

	Returns {
	  "issues":        [issue, ...]  blockers first, then warnings, by item,
	  "blocker_count": int, "warning_count": int,
	  "root_blocked":  {fg: [reason, ...]}  FGs that can't be planned at all
	                   (the issue is on the FG itself),
	  "tree_blocked":  {fg: [reason, ...]}  FGs with a blocker anywhere in their
	                   tree (superset of root_blocked),
	  "boms":          {fg: bom_no}  the BOM each plannable FG resolves to,
	}
	issue = {key, severity, code, item_code, item_name, message, path, fgs, actions}
	with actions = [{label, route}] (desk routes, e.g. "/app/bom/new?item=X")."""
	return _Checker().run(fg_items)


def form_route(doctype, name):
	return "/app/{0}/{1}".format(_slug(doctype), quote(str(name), safe=""))


def new_route(doctype, **values):
	"""Desk route for a new `doctype` form prefilled with `values` (Frappe applies
	the query string as route_options on the new form)."""
	route = "/app/{0}/new".format(_slug(doctype))
	return route + ("?" + urlencode(values) if values else "")


def _slug(doctype):
	return doctype.lower().replace(" ", "-")


class _Checker:
	def __init__(self):
		self.items = {}  # item_code -> Item row (None when not found)
		self.bom_for = {}  # item_code -> resolved default BOM (or None)
		self.bom_rows = {}  # bom -> [BOM Item rows]
		self.issues = {}  # key -> issue
		self.memo = {}  # item_code -> blocker keys in its subtree (incl. itself)
		self.stack = set()  # items on the current walk path (cycle guard)
		self.seen = set()  # every item met in any tree (for the warning pass)
		self.purchase_items = set()

	def run(self, fg_items):
		fg_items = [fg for fg in dict.fromkeys(fg_items) if fg]
		self._load_items(fg_items)

		root_blocked = {}
		tree_blocked = {}
		for fg in fg_items:
			keys = self._walk(fg, [fg])
			for key in keys:
				self.issues[key]["fgs"].add(fg)
			reasons = [self._reason(k) for k in sorted(keys)]
			if reasons:
				tree_blocked[fg] = reasons
			own = [self._reason(k) for k in sorted(keys) if self.issues[k]["item_code"] == fg]
			if own:
				root_blocked[fg] = own

		self._warning_pass()

		issues = sorted(
			self.issues.values(),
			key=lambda i: (0 if i["severity"] == BLOCKER else 1, i["item_code"], i["code"]),
		)
		for i in issues:
			i["fgs"] = sorted(i["fgs"])
		return {
			"issues": issues,
			"blocker_count": sum(1 for i in issues if i["severity"] == BLOCKER),
			"warning_count": sum(1 for i in issues if i["severity"] == WARNING),
			"root_blocked": root_blocked,
			"tree_blocked": tree_blocked,
			"boms": {fg: self.bom_for.get(fg) for fg in fg_items if fg not in root_blocked},
		}

	# ------------------------------------------------------------------ #
	# Tree walk
	# ------------------------------------------------------------------ #
	def _walk(self, item, path):
		"""Blocker keys in `item`'s subtree, item included. Memoised per item, so
		a shared sub-assembly is checked once and its blockers count against every
		FG that uses it; the issue keeps the path it was first found on."""
		if item in self.memo:
			return self.memo[item]
		if item in self.stack or len(path) > MAX_DEPTH:
			return set()
		self.stack.add(item)
		self.seen.add(item)

		keys = self._check_plannable(item, path)
		bom = self.bom_for.get(item)
		if bom and not keys:
			rows = self._bom_children(bom)
			self._load_items([r.item_code for r in rows])
			for r in rows:
				child = self.items.get(r.item_code)
				if not child:
					continue  # ERPNext's explosion joins on Item, so it drops these too
				self.seen.add(r.item_code)
				child_path = path + [r.item_code]
				is_manufacture = child.default_material_request_type == "Manufacture"
				if child.disabled and not is_manufacture:
					self._add(
						WARNING,
						"bom_disabled_item",
						r.item_code,
						child_path,
						_("Disabled item used in BOM {0}").format(bom),
						[
							{"label": _("Open BOM {0}").format(bom), "route": form_route("BOM", bom)},
							self._open_item(r.item_code),
						],
					)
				# Mirrors ERPNext's get_subitems: non-stock components are left out of
				# the plan unless include_non_stock_items is set (FGSRM leaves it off).
				if not child.is_stock_item:
					continue
				if is_manufacture:
					keys = keys | self._walk(r.item_code, child_path)
				elif child.default_material_request_type == "Purchase":
					self.purchase_items.add(r.item_code)

		self.stack.discard(item)
		self.memo[item] = keys
		return keys

	def _check_plannable(self, item, path):
		"""Blocker keys for `item` itself as a plan row (an FG on the root plan, or a
		Manufacture component on a child plan). Resolves its BOM into bom_for."""
		row = self.items.get(item)
		if not row:
			return {self._add(BLOCKER, "item_missing", item, path, _("Item not found"), [])}
		if row.disabled:
			return {
				self._add(BLOCKER, "item_disabled", item, path, _("Item is disabled"), [self._open_item(item)])
			}
		if row.end_of_life and getdate(row.end_of_life) <= getdate(nowdate()):
			return {
				self._add(
					BLOCKER,
					"end_of_life",
					item,
					path,
					_("Item reached end of life on {0}").format(formatdate(row.end_of_life)),
					[self._open_item(item)],
				)
			}
		keys = set()
		if not row.stock_uom:
			keys.add(
				self._add(BLOCKER, "no_uom", item, path, _("Item has no Stock UOM"), [self._open_item(item)])
			)
		bom = self._resolve_bom(row)
		self.bom_for[item] = bom
		if not bom:
			message, actions = self._no_bom_fix(item)
			keys.add(self._add(BLOCKER, "no_bom", item, path, message, actions))
		return keys

	def _resolve_bom(self, row):
		"""Default BOM the plan will use - Item.default_bom when it's active and
		submitted, else the item's (or its template's) active default BOM, matching
		ERPNext's get_item_details."""
		if row.default_bom:
			bom = frappe.db.get_value("BOM", row.default_bom, ["is_active", "docstatus"], as_dict=True)
			if bom and bom.is_active and bom.docstatus == 1:
				return row.default_bom
		for item in (row.name, row.variant_of):
			if not item:
				continue
			bom = frappe.db.get_value(
				"BOM", {"item": item, "is_default": 1, "is_active": 1, "docstatus": 1}, "name"
			)
			if bom:
				return bom
		return None

	def _no_bom_fix(self, item):
		"""(message, actions) for an item without a usable default BOM - point at the
		one-click fix when a draft / inactive / non-default BOM already exists."""
		existing = frappe.get_all(
			"BOM",
			filters={"item": item, "docstatus": ["<", 2]},
			fields=["name", "docstatus", "is_active", "is_default"],
			order_by="modified desc",
			limit=5,
		)
		actions = []
		message = _("No active default BOM")
		for b in existing:
			route = form_route("BOM", b.name)
			if b.docstatus == 0:
				actions.append({"label": _("Submit draft {0}").format(b.name), "route": route})
				message = _("No active default BOM (draft {0} exists)").format(b.name)
				break
			if not b.is_active:
				actions.append({"label": _("Re-activate {0}").format(b.name), "route": route})
				message = _("No active default BOM ({0} is inactive)").format(b.name)
				break
			if not b.is_default:
				actions.append({"label": _("Set {0} as default").format(b.name), "route": route})
				message = _("No default BOM ({0} is active but not default)").format(b.name)
				break
		actions.append({"label": _("Create BOM"), "route": new_route("BOM", item=item)})
		actions.append(self._open_item(item))
		return message, actions

	# ------------------------------------------------------------------ #
	# Warnings
	# ------------------------------------------------------------------ #
	def _warning_pass(self):
		items = sorted(i for i in self.seen if self.items.get(i))
		if not items:
			return

		bin_rate = {
			r.item_code: flt(r.rate)
			for r in frappe.db.sql(
				"""SELECT item_code, MAX(valuation_rate) AS rate FROM `tabBin`
				WHERE item_code IN %(items)s GROUP BY item_code""",
				{"items": items},
				as_dict=True,
			)
		}
		for item in items:
			row = self.items[item]
			if row.disabled or not row.is_stock_item:
				continue
			if not (bin_rate.get(item) or flt(row.valuation_rate) or flt(row.last_purchase_rate)):
				self._add(
					WARNING,
					"no_rate",
					item,
					[item],
					_("No valuation or purchase rate - its cost shows as 0 in the workbook"),
					[self._open_item(item)],
				)

		purchase = sorted(self.purchase_items)
		if purchase:
			with_supplier = {
				r.parent
				for r in frappe.get_all(
					"Item Default",
					filters={"parent": ["in", purchase], "default_supplier": ["is", "set"]},
					fields=["parent"],
				)
			}
			for item in purchase:
				if item not in with_supplier:
					self._add(
						WARNING,
						"no_supplier",
						item,
						[item],
						_("No default supplier - Vendor is blank on the purchase sheet"),
						[self._open_item(item)],
					)

	# ------------------------------------------------------------------ #
	# Helpers
	# ------------------------------------------------------------------ #
	def _add(self, severity, code, item, path, message, actions):
		key = "{0}:{1}".format(code, item)
		if key not in self.issues:
			row = self.items.get(item)
			self.issues[key] = {
				"key": key,
				"severity": severity,
				"code": code,
				"item_code": item,
				"item_name": (row.item_name if row else "") or "",
				"message": message,
				"path": list(path),
				"fgs": set(),
				"actions": actions,
			}
		return key

	def _reason(self, key):
		issue = self.issues[key]
		return "{0}: {1}".format(issue["item_code"], issue["message"])

	def _open_item(self, item):
		return {"label": _("Open Item"), "route": form_route("Item", item)}

	def _load_items(self, codes):
		missing = [c for c in dict.fromkeys(codes) if c and c not in self.items]
		if not missing:
			return
		found = {r.name: r for r in frappe.get_all("Item", filters={"name": ["in", missing]}, fields=_ITEM_FIELDS)}
		for c in missing:
			self.items[c] = found.get(c)

	def _bom_children(self, bom):
		if bom not in self.bom_rows:
			self.bom_rows[bom] = frappe.get_all(
				"BOM Item",
				filters={"parent": bom, "parenttype": "BOM"},
				fields=["item_code"],
				order_by="idx asc",
			)
		return self.bom_rows[bom]
