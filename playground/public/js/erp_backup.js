// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Adds "Create Backup Now" and "Download" actions to the ERP Backup form.
// The heavy work runs in a background job (spec 14) — the button only queues it
// and returns, so the browser never blocks.

frappe.ui.form.on("ERP Backup", {
	refresh(frm) {
		frm.add_custom_button(__("Create Backup Now"), () => create_backup_now());

		const downloadable = frm.doc.archive_path
			&& ["Completed", "Verified"].includes(frm.doc.status);
		if (downloadable) {
			frm.add_custom_button(__("Download"), () => {
				const url = "/api/method/playground.playground.erp_backup.api.download_backup"
					+ "?name=" + encodeURIComponent(frm.doc.name);
				window.open(url, "_blank");
			}).addClass("btn-primary");
		}

		// Live-refresh while a backup is in progress so the stage/status update.
		if (["Queued", "Running"].includes(frm.doc.status)) {
			frm.dashboard.set_headline(
				__("Backup in progress — stage: {0}", [frm.doc.current_stage || frm.doc.status])
			);
			if (!frm.__erp_backup_poll) {
				frm.__erp_backup_poll = setInterval(() => {
					if (["Queued", "Running"].includes(frm.doc.status)) {
						frm.reload_doc();
					} else {
						clearInterval(frm.__erp_backup_poll);
						frm.__erp_backup_poll = null;
					}
				}, 5000);
			}
		} else if (frm.__erp_backup_poll) {
			clearInterval(frm.__erp_backup_poll);
			frm.__erp_backup_poll = null;
		}

		if (frm.doc.status === "Failed") {
			frm.dashboard.set_headline(
				`<span class="text-danger">${__("This backup FAILED at stage: {0}", [frm.doc.current_stage])}</span>`
			);
		}
	},
});

function create_backup_now() {
	const dialog = new frappe.ui.Dialog({
		title: __("Create Backup Now"),
		fields: [
			{
				fieldname: "backup_type", fieldtype: "Select", label: __("Backup Type"),
				options: ["Manual", "Monthly", "Year-End"].join("\n"), default: "Manual", reqd: 1,
			},
			{ fieldname: "description", fieldtype: "Small Text", label: __("Description") },
			{ fieldname: "keep_forever", fieldtype: "Check", label: __("Keep Forever"), default: 0 },
		],
		primary_action_label: __("Start Backup"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method: "playground.playground.erp_backup.api.create_backup_now",
				args: values,
				freeze: true,
				freeze_message: __("Queuing backup…"),
				callback(r) {
					if (r.message && r.message.name) {
						frappe.show_alert({
							message: __("Backup {0} queued.", [r.message.name]), indicator: "green",
						});
						frappe.set_route("Form", "ERP Backup", r.message.name);
					}
				},
			});
		},
	});
	dialog.show();
}
