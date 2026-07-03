frappe.ui.form.on("Work Order", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.bom_no) return;
		frm.add_custom_button(
			__("Update BOM"),
			() => {
				frappe.call({
					method: "playground.playground.doctype.kit_content_mapping.kit_content_mapping.create_from_bom",
					freeze: true,
					freeze_message: __("Loading BOM items into a new Kit Content Mapping…"),
					args: {
						source_bom: frm.doc.bom_no,
						fg_item: frm.doc.production_item,
					},
					callback(r) {
						if (r.message) {
							frappe.set_route("Form", "Kit Content Mapping", r.message);
						}
					},
				});
			},
			__("BOM")
		);
	},
});
