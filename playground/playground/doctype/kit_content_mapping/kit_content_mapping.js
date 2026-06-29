frappe.ui.form.on("Kit Content Mapping", {
	refresh(frm) {
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
						message: __("Created {0} BOM(s), including the FG Item's.", [created.length]),
						indicator: "green",
					});
					frm.reload_doc();
				} else {
					frappe.show_alert({
						message: __(
							"Nothing to generate — the FG Item already has a BOM and every Subassembly New row already has one too, or none are mapped yet."
						),
						indicator: "blue",
					});
				}
			});
		});
	},

	kit_content_framework(frm) {
		if (!frm.doc.kit_content_framework) return;
		frappe.db.get_doc("Kit Content Framework", frm.doc.kit_content_framework).then((framework) => {
			frm.clear_table("mapping_items");
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
			});
			frm.refresh_field("mapping_items");
			frappe.show_alert({
				message: __("Loaded {0} node(s) from \"{1}\".", [
					(framework.items || []).length,
					framework.framework_name,
				]),
				indicator: "blue",
			});
		});
	},
});

frappe.ui.form.on("Kit Content Mapping Item", {
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
		if (!row.bom || row.is_framework_extra) return;
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
