frappe.ui.form.on("Label Print Request", {
	refresh(frm) {
		if (frm.is_new()) return;

		// Reprint: reset this request back to Pending so the print agent picks it up
		// again on its next poll. Available once a request has been Printed or Failed.
		if (["Printed", "Failed"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Reprint Labels"), () => {
				frappe.confirm(
					__("Reset this request to Pending so the printer picks it up again?"),
					() => {
						frappe.call({
							method: "playground.playground.label_printing.reprint",
							args: { name: frm.doc.name },
							freeze: true,
							freeze_message: __("Queuing reprint…"),
							callback: () => frm.reload_doc(),
						});
					}
				);
			});
		}

		if (frm.doc.status === "Failed") {
			frm.dashboard.set_headline_alert(
				__("Last print attempt failed. See Error Log, then use Reprint Labels."),
				"red"
			);
		}
	},
});
