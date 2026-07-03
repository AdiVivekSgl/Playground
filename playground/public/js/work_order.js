frappe.ui.form.on("Work Order", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.bom_no) return;
		frm.add_custom_button(
			__("Request BOM update"),
			() => {
				frappe.call({
					method: "frappe.client.insert",
					freeze: true,
					freeze_message: __("Creating BOM Update Request and pre-loading items…"),
					args: {
						doc: {
							doctype: "BOM Update Request",
							work_order: frm.doc.name,
							fg_item: frm.doc.production_item,
							current_bom: frm.doc.bom_no,
						},
					},
					callback(r) {
						if (r.message && r.message.name) {
							frappe.set_route("Form", "BOM Update Request", r.message.name);
						}
					},
				});
			},
			__("BOM")
		);
	},
});
