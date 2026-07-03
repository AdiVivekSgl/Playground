frappe.ui.form.on("BOM Update Request", {
	refresh(frm) {
		if (frm.doc.kit_content_mapping) {
			frm.add_custom_button(__("Edit mapping ↗"), () => {
				frappe.set_route("Form", "Kit Content Mapping", frm.doc.kit_content_mapping);
			});
		}

		if (!frm.is_new() && frm.doc.status === "Draft") {
			frm.add_custom_button(__("Submit for review"), () => {
				frappe.confirm(
					__("Submit this BOM Update Request for Design / QC review?"),
					() => {
						frm.call("submit_for_review").then(() => {
							frappe.show_alert({ message: __("Submitted for review."), indicator: "blue" });
							frm.reload_doc();
						});
					}
				);
			});
		}

		if (frm.doc.status === "Submitted") {
			frm.add_custom_button(__("Approve"), () => {
				frappe.confirm(
					__("Approve this request and generate BOMs from the mapping?"),
					() => {
						frm.call("approve").then((r) => {
							const created = r.message || [];
							frappe.show_alert({
								message: __(
									"Approved — {0} BOM(s) generated from the mapping.",
									[created.length]
								),
								indicator: "green",
							});
							frm.reload_doc();
						});
					}
				);
			});

			frm.add_custom_button(__("Reject"), () => {
				frappe.prompt(
					{
						label: __("Rejection reason"),
						fieldtype: "Small Text",
						fieldname: "reason",
						reqd: 0,
					},
					(values) => {
						frm.call("reject", { reason: values.reason || "" }).then(() => {
							frappe.show_alert({ message: __("Request rejected."), indicator: "red" });
							frm.reload_doc();
						});
					},
					__("Reject this request"),
					__("Reject")
				);
			});
		}
	},

	current_bom(frm) {
		// Auto-fill FG Item from the selected BOM if not already set.
		if (!frm.doc.current_bom || frm.doc.fg_item) return;
		frappe.db.get_value("BOM", frm.doc.current_bom, "item").then((r) => {
			if (r && r.message && r.message.item) {
				frm.set_value("fg_item", r.message.item);
			}
		});
	},

	work_order(frm) {
		// Auto-fill FG Item and BOM from the selected Work Order.
		if (!frm.doc.work_order) return;
		frappe.db
			.get_value("Work Order", frm.doc.work_order, ["production_item", "bom_no"])
			.then((r) => {
				if (r && r.message) {
					if (!frm.doc.fg_item) frm.set_value("fg_item", r.message.production_item);
					if (!frm.doc.current_bom) frm.set_value("current_bom", r.message.bom_no);
				}
			});
	},
});
