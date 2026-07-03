function show_explosion_dialog(title, lines) {
	if (!lines.length) {
		frappe.msgprint(
			__("Nothing to preview yet — map at least one top-level row with an item code first.")
		);
		return;
	}
	const rows_html = lines
		.map(
			(l) => `
			<tr>
				<td>${frappe.utils.escape_html(l.node || "")}</td>
				<td>${frappe.utils.escape_html(l.item_code || "")}</td>
				<td style="text-align:right">${l.qty}</td>
				<td>${frappe.utils.escape_html(l.uom || "")}</td>
			</tr>`
		)
		.join("");
	const html = `
		<table class="table table-bordered" style="font-size:12px;">
			<thead>
				<tr><th>Node</th><th>Item Code</th><th style="text-align:right">Qty</th><th>UOM</th></tr>
			</thead>
			<tbody>${rows_html}</tbody>
		</table>`;
	new frappe.ui.Dialog({
		title: title,
		size: "large",
		fields: [{ fieldtype: "HTML", options: html }],
	}).show();
}

frappe.ui.form.on("Kit Content Mapping", {
	refresh(frm) {
		// ── Back to BOM Update Request ──────────────────────────────────
		if (frm.doc.bom_update_request) {
			frm.add_custom_button(
				__("← Back to " + frm.doc.bom_update_request),
				() => frappe.set_route("Form", "BOM Update Request", frm.doc.bom_update_request)
			);
		}

		// ── Apply node structure ─────────────────────────────────────────
		if (frm.doc.kit_content_framework && frm.doc.mapping_items && frm.doc.mapping_items.length) {
			frm.add_custom_button(__("Apply node structure"), () => {
				frm.call("apply_node_structure").then((r) => {
					const res = r.message || {};
					const msg = __(
						"{0} node(s) inserted from framework, {1} row(s) unmatched → moved to bottom.",
						[res.inserted || 0, res.unmatched || 0]
					);
					frappe.show_alert({ message: msg, indicator: res.unmatched ? "orange" : "green" });
					frm.reload_doc();
				});
			});
		}

		// ── Node name filter: restrict to nodes in the selected framework ─
		frm.set_query("node_name", "mapping_items", () => {
			if (!frm._framework_node_names || !frm._framework_node_names.length) return {};
			return { filters: [["name", "in", frm._framework_node_names]] };
		});

		frm.set_query("bom", "mapping_items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				filters: {
					item: row.item_code,
					is_active: 1,
					docstatus: 1,
				},
			};
		});

		frm.add_custom_button(__("Generate BOMs"), () => {
			frm.call({ method: "generate_pending_boms", doc: frm.doc }).then((r) => {
				const created = r.message || [];
				if (created.length) {
					frappe.show_alert({
						message: __(
							"Created {0} BOM(s) — including the FG Item's full level set (L1 default, plus alternates) where applicable.",
							[created.length]
						),
						indicator: "green",
					});
					frm.reload_doc();
				} else {
					frappe.show_alert({
						message: __(
							"Nothing to generate — the FG Item's BOM set already exists and every Subassembly New row already has one too, or none are mapped yet."
						),
						indicator: "blue",
					});
				}
			});
		});

		frm.add_custom_button(__("Preview Fully Exploded FG BOM"), () => {
			frm.call({ method: "preview_fully_exploded_fg_bom", doc: frm.doc }).then((r) => {
				show_explosion_dialog(
					__("Fully Exploded FG BOM — Preview (nothing saved)"),
					r.message || []
				);
			});
		});

		frm.add_custom_button(__("Preview Custom Exploded BOM"), () => {
			frm.call({ method: "preview_custom_exploded_fg_bom", doc: frm.doc }).then((r) => {
				show_explosion_dialog(
					__("Custom Exploded FG BOM — Preview (nothing saved)"),
					r.message || []
				);
			});
		});

		frm.add_custom_button(__("Generate Custom Exploded BOM"), () => {
			frm.call({ method: "generate_custom_exploded_bom", doc: frm.doc }).then((r) => {
				if (r.message) {
					frappe.show_alert({
						message: __("Created Custom Exploded BOM: {0}", [r.message]),
						indicator: "green",
					});
					frm.reload_doc();
				}
			});
		});
	},

	kit_content_framework(frm) {
		if (!frm.doc.kit_content_framework) return;
		frappe.db.get_doc("Kit Content Framework", frm.doc.kit_content_framework).then((framework) => {
			frm.clear_table("mapping_items");
			// Cache framework node names so the node_name Link query can filter.
			frm._framework_node_names = (framework.items || []).map((fi) => fi.node_name);

			(framework.items || []).forEach((fi) => {
				const row = frm.add_child("mapping_items");
				row.node_name = fi.node_name;
				row.indent_level = fi.indent_level;
				row.framework_node_type = fi.node_type;
				row.treatment =
					fi.node_type === "Passthrough"
						? "Passthrough"
						: fi.node_type === "Subassembly"
						? "Subassembly Existing"
						: "";
				// Copy keep_aggregated from the framework row as a starting default
				// — the user can override it freely in the mapping from here on.
				row.keep_aggregated = fi.keep_aggregated || 0;
			});
			frm.refresh_field("mapping_items");
			frappe.show_alert({
				message: __("Loaded {0} node(s) from \"{1}\".", [
					(framework.items || []).length,
					framework.framework_name,
				]),
				indicator: "blue",
			});

			// Rows just loaded are mostly blank (item_code, bom) — a normal
			// save would fail mandatory validation on nearly every row. This
			// relaxed save just gets the document a real name immediately;
			// the ordinary Save button still enforces every mandatory field
			// the regular way once the user actually fills the rows in.
			frm.call({ method: "save_relaxed", doc: frm.doc }).then((r) => {
				if (r.message && r.message !== frm.doc.name) {
					// Navigate explicitly rather than frm.reload_doc() — we've
					// already hit a case where reloading right after a
					// same-request rename used a name the form hadn't
					// picked up yet. A route change always loads correctly.
					frappe.set_route("Form", "Kit Content Mapping", r.message);
				}
			});
		});
	},
});

frappe.ui.form.on("Kit Content Mapping Item", {
	unlock_components(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.unlock_components) return;
		// Unlock this row's own "Other" children (the rows added when its BOM
		// was selected) so Type / Treatment / Item Code / Qty become editable.
		// This checkbox locks itself once checked (read_only_depends_on on the
		// field) — there's no path back to the locked state.
		(frm.doc.mapping_items || []).forEach((child) => {
			if (child.is_framework_extra && child.bom_source_row === row.name) {
				frappe.model.set_value(child.doctype, child.name, "is_editable", 1);
			}
		});
		frm.refresh_field("mapping_items");
	},

	treatment(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		// Passthrough never carries an item or BOM, regardless of what the row held before.
		if (row.treatment === "Passthrough") {
			frappe.model.set_value(cdt, cdn, "item_code", "");
			frappe.model.set_value(cdt, cdn, "bom", "");
		}
		frm.refresh_field("mapping_items");
	},

	bom(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.bom) return;
		// Ordinary extra ("Other") rows never get a bom picked on them directly
		// — but once unlocked via the parent's "Allow Editing Components"
		// checkbox, a user can retype Treatment to Subassembly Existing/New on
		// one of them, at which point it needs to behave exactly like any
		// other Subassembly Existing row, including triggering its own
		// explode-and-diff recursively.
		if (row.is_framework_extra && !row.is_editable) return;

		// explode_bom_for_row does a self.save() server-side. If this Mapping
		// document has never been saved before, that save is actually the
		// document's first insert — Frappe assigns it its real permanent name
		// at that moment. The form in the browser doesn't know about that
		// rename, so frm.reload_doc() afterward tries to fetch the OLD
		// (now nonexistent) placeholder name and silently does nothing —
		// the rows really do get saved, but the page never shows them.
		// Simplest reliable fix: require an explicit save first.
		if (frm.is_new()) {
			frappe.model.set_value(cdt, cdn, "bom", "");
			frappe.msgprint({
				title: __("Save first"),
				message: __(
					"Please save this Kit Content Mapping before selecting a BOM — picking a BOM on an unsaved document can save it under a new name the form doesn't know about yet, so nothing reloads correctly."
				),
				indicator: "orange",
			});
			return;
		}

		frm.call({
			method: "explode_bom_for_row",
			doc: frm.doc,
			args: { row_name: row.name, bom_name: row.bom },
		})
			.then((r) => {
				const extra = r.message || [];
				if (extra.length) {
					frappe.show_alert({
						message: __(
							"{0} component(s) in that BOM aren't in the framework — added below, flagged \"Other\", recorded on this mapping only.",
							[extra.length]
						),
						indicator: "orange",
					});
				}
				frm.reload_doc();
			})
			.catch(() => {
				// Server rejected it (e.g. BOM doesn't belong to this row's item) —
				// don't leave the field showing a selection that was never saved.
				frappe.model.set_value(cdt, cdn, "bom", "");
			});
	},
});
