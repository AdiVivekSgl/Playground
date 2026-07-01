import frappe
from frappe import _
from frappe.model.document import Document


class KitContentMapping(Document):
	def validate(self):
		# Runs before Frappe's own _validate_links(), so any Item we create
		# here already exists by the time the item_code Link field is checked.
		self._auto_create_new_items()

	@frappe.whitelist()
	def save_relaxed(self):
		"""Save while ignoring mandatory-field validation on rows the user
		hasn't filled in yet (item_code, bom). Used right after loading or
		reloading mapping_items from a Kit Content Framework, so the
		document gets — or keeps — a real saved name immediately, instead
		of leaving the user stuck on an unsaved draft until every single
		row is filled in. This flag only affects this one in-memory save;
		the ordinary Save button still enforces every mandatory field as
		normal, since a fresh Document instance is loaded for that."""
		self.flags.ignore_mandatory = True
		self.save()
		return self.name

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
		explosion against what the framework expects.

		Deliberately excludes is_framework_extra rows: those are "Other"
		rows injected by a PREVIOUS BOM selection on this same row, tagged
		framework_node_type="Purchase" purely so they display like an
		ordinary purchase row. If they counted toward this baseline, a
		second BOM pick on the same row would compare the new BOM against
		a baseline polluted with the old BOM's leftovers, instead of
		against what the framework template actually defines."""
		rows = self._ordered_rows()
		idx = rows.index(target_row)
		level = target_row.indent_level
		codes = set()
		for row in rows[idx + 1 :]:
			if row.indent_level <= level:
				break
			if row.is_framework_extra:
				continue
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

			# Build the rest of the level set (L2 ... L(max_depth-1), then
			# Fully Exploded) in this SAME one-shot pass. Gated behind the
			# same `not self.fg_bom` check above — once fg_bom exists, this
			# whole block (L1 included) is skipped on future clicks.
			indent_levels = [r.indent_level for r in rows if r.indent_level]
			max_level = max(indent_levels) if indent_levels else 1
			# L(max_level) would, by construction, already be fully exploded
			# (nothing in the tree goes deeper), so the partial levels only
			# run up to max_level - 1; Fully Exploded covers that last step.
			for level in range(2, max_level):
				level_bom_name = self._generate_level_bom(rows, level, "L{0}".format(level))
				created.append(level_bom_name)
			fully_exploded_name = self._generate_level_bom(rows, None, "Fully Exploded")
			created.append(fully_exploded_name)

		for row in rows:
			if row.treatment == "Subassembly New" and row.item_code and not row.bom:
				bom_name = self._generate_bom_for_row(rows, row)
				row.bom = bom_name
				created.append(bom_name)

		for row in rows:
			# Rows whose components were unlocked (and presumably edited) get a
			# FRESH BOM every time this button is clicked, regardless of
			# whether they already have one — that's deliberate: this is the
			# user's explicit "materialize my edits" action, not a one-shot
			# fill-in-the-blank like Subassembly New above. The originally
			# selected BOM is never touched; this just creates a new one in
			# parallel and repoints `bom` to it, marked as the new default
			# for this Item. Clicking again after further edits creates
			# another new BOM superseding this one — the previous generated
			# BOM isn't deleted, just no longer referenced or default.
			if (
				row.treatment == "Subassembly Existing"
				and getattr(row, "unlock_components", 0)
				and row.item_code
			):
				bom_name = self._generate_bom_for_row(rows, row, is_default=True)
				row.bom = bom_name
				created.append(bom_name)

		if created:
			self.save()
		return created

	def _build_bom(self, bom_item, component_qty_pairs, rows, is_default=False, explosion_level=None):
		"""`component_qty_pairs` is a list of (row, qty) tuples. qty is the
		EFFECTIVE quantity for this specific BOM — for a normal single-level
		build that's just the row's own qty; for a depth-bounded or fully
		exploded build it's already been multiplied through every collapsed
		level above it (see _explode_to_depth).

		`explosion_level`: comma-separated names of Subassembly nodes that were
		kept aggregated for this BOM (Custom Exploded BOMs only). Written to
		the BOM's custom explosion_level field so the aggregation choices are
		visible directly on the BOM document without having to cross-reference
		the mapping. Empty string for fully-exploded and depth-level BOMs."""
		bom = frappe.new_doc("BOM")
		bom.item = bom_item
		bom.quantity = 1
		if is_default:
			bom.is_default = 1
		if explosion_level is not None:
			bom.explosion_level = explosion_level
		for component, qty in component_qty_pairs:
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
					"qty": qty or 1,
					"uom": uom,
					# node is now a Link to Kit Content Node — store the node
					# name directly rather than a computed breadcrumb path.
					"node": component.node_name,
				},
			)
		bom.insert()
		return bom.name

	def _generate_bom_for_row(self, rows, target_row, is_default=False):
		components = self._resolve_components(rows, target_row)
		if not components:
			frappe.throw(
				_(
					"Row #{0} ({1}) has no resolvable components yet — map its "
					"child rows (item code + qty) before generating its BOM."
				).format(target_row.idx, target_row.node_name)
			)
		pairs = [(c, c.qty or 1) for c in components]
		return self._build_bom(target_row.item_code, pairs, rows, is_default=is_default)

	def _generate_fg_bom(self, rows):
		components = self._resolve_root_components(rows)
		if not components:
			frappe.throw(_("No top-level mapping rows found to build the FG Item's BOM."))
		pairs = [(c, c.qty or 1) for c in components]
		# is_default=True: this is "L1", the one BOM of the generated set that
		# should actually be the Item's default for work orders/costing — the
		# L2...Ln and Fully Exploded variants below are alternates only.
		return self._build_bom(self.fg_item, pairs, rows, is_default=True)

	def _explode_to_depth(self, rows, max_depth):
		"""Walk the FG root's tree, multiplying quantities through each
		collapsed level. A Subassembly node becomes a line (not recursed
		into further) once max_depth is reached; max_depth=None means never
		stop early — recurse all the way to true leaves (Fully Exploded).
		Returns a list of (row, effective_qty) tuples, ready to hand
		straight to _build_bom."""
		lines = []

		def walk(component, multiplier, depth):
			qty = (component.qty or 1) * multiplier
			is_leaf_type = component.framework_node_type != "Subassembly"
			if is_leaf_type or (max_depth is not None and depth >= max_depth):
				lines.append((component, qty))
				return
			children = self._resolve_components(rows, component)
			if not children:
				frappe.throw(
					_(
						"Row #{0} ({1}) has no mapped child rows, so it can't "
						"be exploded further for this BOM level. Map its "
						"children first, or this branch can't go deeper than "
						"where it currently stops."
					).format(component.idx, component.node_name)
				)
			for child in children:
				walk(child, qty, depth + 1)

		for root_child in self._resolve_root_components(rows):
			walk(root_child, 1, 1)

		return lines

	def _generate_level_bom(self, rows, max_depth, label):
		lines = self._explode_to_depth(rows, max_depth)
		bom_name = self._build_bom(self.fg_item, lines, rows, is_default=False)
		self.append("generated_boms", {"level_label": label, "bom": bom_name})
		return bom_name

	@frappe.whitelist()
	def preview_fully_exploded_fg_bom(self):
		"""Read-only: computes the same Fully Exploded line list that
		Generate BOMs would persist, but never saves or inserts anything —
		safe to call any time, even before Generate BOMs has ever been
		clicked, purely to sanity-check the tree."""
		if not self.fg_item:
			frappe.throw(_("Set an FG Item before previewing its BOM."))
		rows = self._ordered_rows()
		lines = self._explode_to_depth(rows, None)
		return [
			{
				"node": self._node_path(rows, component),
				"item_code": component.item_code,
				"qty": qty,
				"uom": component.uom
				or frappe.db.get_value("Item", component.item_code, "stock_uom"),
			}
			for component, qty in lines
		]

	def _explode_selective(self, rows):
		"""Like _explode_to_depth, but the stopping rule isn't a uniform
		depth count — each Subassembly row decides for ITSELF whether it
		stays aggregated (its `keep_aggregated` checkbox) or gets exploded
		further. Different branches can stop at completely different
		points: a vendor-supplied kit can stay a single line while
		everything else in the same tree explodes all the way to raw
		materials. Default (unchecked) is "explode" — you opt specific
		nodes OUT, rather than opting branches in.

		Returns (lines, kept_names) where `kept_names` is a list of
		node_name values for Subassembly rows that were kept aggregated —
		used to populate BOM.explosion_level on generated Custom BOMs."""
		lines = []
		kept_names = []

		def walk(component, multiplier):
			qty = (component.qty or 1) * multiplier
			is_leaf_type = component.framework_node_type != "Subassembly"
			if is_leaf_type or getattr(component, "keep_aggregated", 0):
				lines.append((component, qty))
				if component.framework_node_type == "Subassembly" and getattr(component, "keep_aggregated", 0):
					kept_names.append(component.node_name)
				return
			children = self._resolve_components(rows, component)
			if not children:
				frappe.throw(
					_(
						"Row #{0} ({1}) has no mapped child rows, so it can't "
						"be exploded further. Map its children, or check "
						"\"Keep Aggregated\" on this row instead."
					).format(component.idx, component.node_name)
				)
			for child in children:
				walk(child, qty)

		for root_child in self._resolve_root_components(rows):
			walk(root_child, 1)

		return lines, kept_names

	@frappe.whitelist()
	def preview_custom_exploded_fg_bom(self):
		"""Read-only counterpart to preview_fully_exploded_fg_bom, but using
		the per-node keep_aggregated selections instead of a uniform depth."""
		if not self.fg_item:
			frappe.throw(_("Set an FG Item before previewing its BOM."))
		rows = self._ordered_rows()
		lines, _kept = self._explode_selective(rows)
		return [
			{
				"node": component.node_name,
				"item_code": component.item_code,
				"qty": qty,
				"uom": component.uom
				or frappe.db.get_value("Item", component.item_code, "stock_uom"),
			}
			for component, qty in lines
		]

	@frappe.whitelist()
	def generate_custom_exploded_bom(self):
		"""Unlike the L1...Fully Exploded set, this always regenerates on
		every click rather than one-shot — the whole point is to reflect
		whatever the current keep_aggregated checkboxes say right now, and
		that can change at any time. Never marked default; tracked in
		generated_boms like the other alternates, labeled "Custom"."""
		if not self.fg_item:
			frappe.throw(_("Set an FG Item before generating its BOM."))
		rows = self._ordered_rows()
		lines, kept_names = self._explode_selective(rows)
		explosion_level = ", ".join(kept_names) if kept_names else ""
		bom_name = self._build_bom(
			self.fg_item, lines, rows, is_default=False, explosion_level=explosion_level
		)
		self.append("generated_boms", {"level_label": "Custom", "bom": bom_name})
		self.save()
		return bom_name

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

		final_rows = []
		for r in self._ordered_rows():
			if r.is_framework_extra and r.bom_source_row == row.name:
				continue  # drop stale extras from a previous BOM pick on this row
			row_dict = r.as_dict()
			row_dict.pop("idx", None)  # let append() assign sequential idx as we go
			final_rows.append(row_dict)
			if r.name == row.name:
				for code in extra_codes:
					d = exploded[code]
					final_rows.append(
						{
							# Reflect the PARENT (the Subassembly Existing row whose BOM
						# was exploded), not the exploded child's own name — the Item
						# Code column already shows the child; the Node column is more
						# useful telling you which subassembly this extra came from.
						"node_name": row.node_name,
							"indent_level": row.indent_level + 1,
							"framework_node_type": "Purchase",
							"treatment": "Other",
							"item_code": code,
							"qty": d.stock_qty,
							"uom": d.stock_uom,
							"is_framework_extra": 1,
							"bom_source_row": row.name,
						}
					)

		self.set("mapping_items", [])
		for row_dict in final_rows:
			self.append("mapping_items", row_dict)
		self.save()
		return extra_codes
