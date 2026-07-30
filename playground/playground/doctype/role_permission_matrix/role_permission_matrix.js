// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.ui.form.on("Role Permission Matrix", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Validate Matrix"), () => validate_matrix(frm), __("Actions"));
		frm.add_custom_button(__("Apply Permissions"), () => apply_permissions(frm), __("Actions")).addClass(
			"btn-primary"
		);
		frm.add_custom_button(__("Export Live Permissions"), () => export_live_permissions(), __("Tools"));
		frm.add_custom_button(__("Import from Excel"), () => import_from_excel(frm), __("Tools"));

		if (frm.doc.status === "Applied" && frm.doc.applied_on) {
			frm.dashboard.set_headline(
				__("Applied {0} by {1}", [
					frappe.datetime.str_to_user(frm.doc.applied_on),
					frm.doc.applied_by,
				])
			);
		}
	},
});

function has_rows(frm) {
	if ((frm.doc.permissions_table || []).length) return true;
	frappe.msgprint({ message: __("No permission rows defined in the matrix."), indicator: "orange" });
	return false;
}

// Persist any pending grid edits so the server acts on saved data.
function ensure_saved(frm) {
	return frm.is_dirty() ? frm.save() : Promise.resolve();
}

function validate_matrix(frm) {
	if (!has_rows(frm)) return;
	ensure_saved(frm).then(() => {
		frappe.call({
			method: "playground.playground.doctype.role_permission_matrix.role_permission_matrix.validate_matrix",
			args: { docname: frm.doc.name },
			freeze: true,
			freeze_message: __("Validating…"),
			callback(r) {
				const m = r.message || {};
				if (m.valid) {
					frappe.msgprint({
						title: __("Valid"),
						message: __("✓ Matrix is valid and ready to apply."),
						indicator: "green",
					});
				} else {
					frappe.msgprint({
						title: __("Validation Failed"),
						message: (m.errors || []).map((e) => "❌ " + e).join("<br>"),
						indicator: "red",
					});
				}
			},
		});
	});
}

function apply_permissions(frm) {
	if (!has_rows(frm)) return;
	frappe.confirm(
		__(
			"This will create or update Custom DocPerm records (and Role Profiles) for every row in the matrix. Existing standard permissions for these DocTypes are converted to custom permissions. Proceed?"
		),
		() => {
			ensure_saved(frm).then(() => {
				frappe.call({
					method: "playground.playground.doctype.role_permission_matrix.role_permission_matrix.apply_permissions",
					args: { docname: frm.doc.name },
					freeze: true,
					freeze_message: __("Applying permissions…"),
					callback(r) {
						const m = r.message || {};
						if (!m.success) return;
						frm.reload_doc();
						frappe.msgprint({
							title: __("Permissions Applied"),
							indicator: "green",
							message: __(
								"Updated {0} permission row(s) across {1} DocType(s). Role Profiles synced: {2}.",
								[m.rows || 0, (m.doctypes || []).length, m.profiles || 0]
							),
						});
					},
				});
			});
		}
	);
}

// Download the live custom permissions as .xlsx. The server sets a binary file
// response, so this must be a form POST (open_url_post), not an ajax frappe.call.
function export_live_permissions() {
	open_url_post(
		"/api/method/playground.playground.doctype.role_permission_matrix.role_permission_matrix.export_live_permissions",
		{}
	);
}

function import_from_excel(frm) {
	if (!frm.doc.upload_excel) {
		frappe.msgprint({
			message: __("Attach an Excel file in 'Upload Excel' first."),
			indicator: "orange",
		});
		return;
	}
	frappe.confirm(
		__("This replaces every row in the matrix below with the contents of the uploaded file. Continue?"),
		() => {
			// Persist the attachment before the server reads it.
			ensure_saved(frm).then(() => {
				frappe.call({
					method: "playground.playground.doctype.role_permission_matrix.role_permission_matrix.import_from_excel",
					args: { docname: frm.doc.name },
					freeze: true,
					freeze_message: __("Reading Excel…"),
					callback(r) {
						const m = r.message || {};
						frm.reload_doc();
						frappe.show_alert({
							message: __("Imported {0} row(s) into the matrix.", [m.rows || 0]),
							indicator: "green",
						});
					},
				});
			});
		}
	);
}
