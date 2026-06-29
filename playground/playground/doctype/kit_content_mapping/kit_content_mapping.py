import frappe
from frappe import _
from frappe.model.document import Document


class KitContentMapping(Document):
	def validate(self):
		# Runs before Frappe's own _validate_links(), so any Item we create
		# here already exists by the time the item_code Link field is checked.
		self._auto_create_new_items()

	# ------------------------------------------------------------------ #
	# Subassembly New -> auto-create the Item (BOM is a separate step)
	# ------------------------------------------------------------------ #
	def _auto_create_new_items(self):
		for row in self.mapping_items:
			if row.treatment != "Subassembly New" or not row.item_code:
				continue
			if frappe.db.exists("Item", row.item_code):
				continue
			if not self.default_item_group or not self.default_stock_uom:
				frappe.throw(
					_(
						"Row #{0} ({1}): Item {2} does not exist yet. Set "
						"Default Item Group and Default Stock UOM on this Mapping "
						"so it can be created automatically, or create the Item "
						"yourself first."
					).format(row.idx, row.node_name, row.item_code)
				)
			item = frappe.new_doc("Item")
			item.item_code = row.item_code
			item.item_name = row.node_name
			item.item_group = self.default_item_group
			item.stock_uom = self.default_stock_uom
			item.is_stock_item = 1
			item.insert(ignore_permissions=True)

	# ------------------------------------------------------------------ #
	# Helpers for walking the mapping tree
	# ------------------------------------------------------------------ #
	def _ordered_rows(self):
		return sorted(self.mapping_items, key=lambda r: r.idx)

	def _get_row(self, row_name):
		for row in self.mapping_items:
			if row.name == row_name:
				return row
		frappe.throw(_("Row {0} not found in Mapping Items.").format(row_name))

	def _direct_children(self, rows, target_row):
		idx = rows.index(target_row)
		level = target_row.indent_level
		children = []
		for row in rows[idx + 1 :]:
			if row.indent_level <= level:
				break
			if row.indent_level == level + 1:
				children.append(row)
		return children

	def _resolve_components(self, rows, target_row):
		"""Direct children of target_row, with Passthrough children exploded
		recursively to their nearest real (Purchase/Subassembly) descendants —
		same promotion rule used everywhere else in this app."""
		resolved = []
		for child in self._direct_children(rows, target_row):
			if child.framework_node_type == "Passthrough":
				resolved.extend(self._resolve_components(rows, child))
			else:
				resolved.append(child)
		return resolved

	def _resolve_root_components(self, rows):
		"""Like _resolve_components, but for the FG Item's implicit root:
		its direct children are the level-1 rows, with Passthrough ones
		promoted to their nearest real descendants."""
		resolved = []
		for child in rows:
			if child.indent_level != 1:
				continue
			if child.framework_node_type == "Passthrough":
				resolved.extend(self._resolve_components(rows, child))
			else:
				resolved.append(child)
		return resolved

	def _purchase_item_codes_under(self, target_row):
		"""All Purchase-level item codes anywhere in target_row's subtree —
		used as the comparison set when diffing an existing BOM's full
		explosion against what the framework expects."""
		rows = self._ordered_rows()
		idx = rows.index(target_row)
		level = target_row.indent_level
		codes = set()
		for row in rows[idx + 1 :]:
			if row.indent_level <= level:
				break
			if row.framework_node_type == "Purchase" and row.item_code:
				codes.add(row.item_code)
		return codes

	def _node_path(self, rows, target_row):
		"""Breadcrumb of node names from the framework root down to target_row,
		e.g. 'KWL Kit > HS Components > Core Tubings'. Written onto the
		generated BOM Item's `node` field for traceability."""
		idx = rows.index(target_row)
		path = [target_row.node_name]
		current_level = target_row.indent_level
		for row in reversed(rows[:idx]):
			if row.indent_level < current_level:
				path.append(row.node_name)
				current_level = row.indent_level
			if current_level <= 1:
				break
		return " > ".join(reversed(path))

	# ------------------------------------------------------------------ #
	# Generate BOMs for the FG Item and every pending "Subassembly New"
	# row, in one shot — the full multi-level explosion.
	# ------------------------------------------------------------------ #
	@frappe.whitelist()
	def generate_pending_boms(self):
		"""Item-existence is guaranteed for every row by the time this is
		called (validate() already ran), and one Subassembly's BOM never
		depends on whether another Subassembly's BOM exists yet — each BOM
		only needs its own direct children's item codes — so these can be
		generated in any order, including in a single pass like this. The
		FG Item's own BOM is the same: it only needs the level-1 rows'
		item codes, not whether those rows' own BOMs exist yet."""
		rows = self._ordered_rows()
		created = []

		if self.fg_item and not self.fg_bom:
			fg_bom_name = self._generate_fg_bom(rows)
			self.fg_bom = fg_bom_name
			created.append(fg_bom_name)

		for row in rows:
			if row.treatment == "Subassembly New" and row.item_code and not row.bom:
				bom_name = self._generate_bom_for_row(rows, row)
				row.bom = bom_name
				created.append(bom_name)
		if created:
			self.save()
		return created

	def _build_bom(self, bom_item, components, rows):
		bom = frappe.new_doc("BOM")
		bom.item = bom_item
		bom.quantity = 1
		for component in components:
			if not component.item_code:
				frappe.throw(
					_(
						"Row #{0} ({1}) needs an Item Code before {2}'s BOM can be generated."
					).format(component.idx, component.node_name, bom_item)
				)
			uom = component.uom or frappe.db.get_value("Item", component.item_code, "stock_uom")
			bom.append(
				"items",
				{
					"item_code": component.item_code,
					"qty": component.qty or 1,
					"uom": uom,
					"node": self._node_path(rows, component),
				},
			)
		bom.insert()
		return bom.name

	def _generate_bom_for_row(self, rows, target_row):
		components = self._resolve_components(rows, target_row)
		if not components:
			frappe.throw(
				_(
					"Row #{0} ({1}) has no resolvable components yet — map its "
					"child rows (item code + qty) before generating its BOM."
				).format(target_row.idx, target_row.node_name)
			)
		return self._build_bom(target_row.item_code, components, rows)

	def _generate_fg_bom(self, rows):
		components = self._resolve_root_components(rows)
		if not components:
			frappe.throw(_("No top-level mapping rows found to build the FG Item's BOM."))
		return self._build_bom(self.fg_item, components, rows)

	# ------------------------------------------------------------------ #
	# Existing BOM selected -> explode fully, diff against the framework,
	# record (not write back) anything the framework didn't anticipate
	# ------------------------------------------------------------------ #
	@frappe.whitelist()
	def explode_bom_for_row(self, row_name, bom_name):
		row = self._get_row(row_name)

		bom_item = frappe.db.get_value("BOM", bom_name, "item")
		if bom_item != row.item_code:
			frappe.throw(
				_(
					"Row #{0} ({1}): {2} is a BOM for {3}, not {4}. Pick a BOM that "
					"actually belongs to this row's Item Code."
				).format(row.idx, row.node_name, bom_name, bom_item, row.item_code)
			)

		row.bom = bom_name
		self.save()
		# `row` is still the same in-memory child object after save() — if it
		# was a brand-new, not-yet-saved row (client-side temp name like
		# "new-kit-content-mapping-item-xxxxx"), Frappe updates row.name to
		# its real permanent name in place right here. We deliberately do
		# NOT reload() or re-fetch by the old row_name: reload() discards
		# this object and re-fetches fresh ones from the DB, and the
		# original row_name argument is now stale and matches nothing.

		bom_doc = frappe.get_doc("BOM", bom_name)
		exploded = {d.item_code: d for d in bom_doc.exploded_items}
		framework_codes = self._purchase_item_codes_under(row)
		extra_codes = [code for code in exploded if code not in framework_codes]

		# Drop any stale extras from a previously selected BOM on this same row.
		self.mapping_items = [
			r
			for r in self.mapping_items
			if not (r.is_framework_extra and r.bom_source_row == row.name)
		]
		# `row` is unaffected by the filter above (it's never an
		# is_framework_extra row when this method runs) and its `.name` is
		# already current from the save() above — no need to re-fetch it by
		# the (possibly stale) row_name.

		insert_pos = self.mapping_items.index(row) + 1
		new_rows = []
		for code in extra_codes:
			d = exploded[code]
			new_row = self.append("mapping_items", {})
			new_row.node_name = d.item_name or code
			new_row.indent_level = row.indent_level + 1
			new_row.framework_node_type = "Purchase"
			new_row.treatment = "Other"
			new_row.item_code = code
			new_row.qty = d.stock_qty
			new_row.uom = d.stock_uom
			new_row.is_framework_extra = 1
			new_row.bom_source_row = row.name
			new_rows.append(new_row)
			self.mapping_items.remove(new_row)

		self.mapping_items[insert_pos:insert_pos] = new_rows
		for i, r in enumerate(self.mapping_items, start=1):
			r.idx = i
		self.save()
		return extra_codes
