frappe.ui.form.on("BOM Item Replacement Tool", {
	refresh(frm) {
		if (frm.doc.status !== "Completed") {
			frm.add_custom_button(__("Generate Preview"), () => frm.call("generate_preview").then(() => frm.reload_doc()));
		}

		if (frm.doc.status === "Preview Generated") {
			frm.add_custom_button(__("Execute Replacement"), () => {
				frappe.confirm(
					__("This operation will cancel and amend active BOMs. Continue?"),
					() => frm.call("enqueue_replacement").then(() => frm.reload_doc())
				);
			});
		}

		const new_boms = (frm.doc.replacement_details || []).filter((row) => row.new_bom);
		if (new_boms.length) {
			frm.add_custom_button(__("Open New BOM"), () => frappe.set_route("Form", "BOM", new_boms[0].new_bom));
		}
	},
});
