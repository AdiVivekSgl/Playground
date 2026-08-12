// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt
//
// Adds "Export / Import Permissions Workbook" to the Role Profile form. Export
// streams a 3-sheet .xlsx (Roles / Permissions / Roll-up); import previews the
// diff (mirror semantics) and only writes after the user confirms.
// Backend: playground/playground/role_profile_permissions.py

const RPP_METHOD = "playground.playground.role_profile_permissions";

frappe.ui.form.on("Role Profile", {
	refresh(frm) {
		if (frm.is_new() || !frappe.user.has_role("System Manager")) return;

		frm.add_custom_button(
			__("Export Workbook"),
			() => {
				open_url_post(`/api/method/${RPP_METHOD}.export_workbook`, {
					role_profile: frm.doc.name,
				});
			},
			__("Permissions")
		);

		frm.add_custom_button(__("Import Workbook"), () => rpp_import(frm), __("Permissions"));
	},
});

function rpp_import(frm) {
	frappe.prompt(
		[{ fieldtype: "Attach", fieldname: "file", label: __("Workbook (.xlsx)"), reqd: 1 }],
		(values) => rpp_preview(frm, values.file),
		__("Import Permissions Workbook"),
		__("Review Changes")
	);
}

function rpp_preview(frm, file_url) {
	frappe.call({
		method: `${RPP_METHOD}.preview_import`,
		args: { role_profile: frm.doc.name, file_url },
		freeze: true,
		freeze_message: __("Analysing workbook…"),
		callback(r) {
			const d = r.message || {};
			if (!d.ok) {
				frappe.msgprint({
					title: __("Cannot Import"),
					indicator: "red",
					message: (d.errors || []).map((e) => "❌ " + e).join("<br>"),
				});
				return;
			}
			rpp_show_diff(frm, file_url, d);
		},
	});
}

function rpp_show_diff(frm, file_url, d) {
	const dlg = new frappe.ui.Dialog({
		title: __("Review Permission Changes"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Apply Changes"),
		primary_action() {
			dlg.hide();
			// Pass the previewed file's hash so the backend rejects a swapped file.
			rpp_apply(frm, file_url, d.file_hash);
		},
	});
	dlg.fields_dict.body.$wrapper.html(rpp_render(d));
	dlg.show();
}

function rpp_apply(frm, file_url, expected_hash) {
	frappe.call({
		method: `${RPP_METHOD}.apply_import`,
		args: { role_profile: frm.doc.name, file_url, expected_hash },
		freeze: true,
		freeze_message: __("Applying changes…"),
		callback(r) {
			const m = r.message || {};
			if (!m.success) return;
			frm.reload_doc();
			const profiles = (m.profiles_touched || []).join(", ");
			let msg = __("Roles +{0} / -{1}. Permissions: {2} added, {3} updated, {4} removed.", [
				m.membership_added,
				m.membership_removed,
				m.perms_added,
				m.perms_updated,
				m.perms_removed,
			]);
			if (profiles) msg += "<br>" + __("Profiles updated: {0}.", [frappe.utils.escape_html(profiles)]);
			if (m.log) msg += "<br>" + __("Logged as {0}.", [frappe.utils.escape_html(m.log)]);
			frappe.msgprint({ title: __("Applied"), indicator: "green", message: msg });
		},
	});
}

// ---- diff rendering -------------------------------------------------------

function rpp_render(d) {
	const esc = frappe.utils.escape_html;
	const parts = [];

	const memberships = d.memberships || [];
	const memberRemovals = memberships.filter((m) => m.remove.length);
	const memberAdditions = memberships.filter((m) => m.add.length && !m.is_new);
	const newProfiles = memberships.filter((m) => m.is_new);

	const nothing =
		!memberRemovals.length &&
		!memberAdditions.length &&
		!newProfiles.length &&
		!d.perm_add.length &&
		!d.perm_update.length &&
		!d.perm_remove.length;
	if (nothing) {
		return `<p>${__("No differences — the workbook matches the current state.")}</p>`;
	}

	// Deletions first, loudly.
	if (memberRemovals.length || d.perm_remove.length) {
		const rows = [];
		memberRemovals.forEach((m) => {
			rows.push(
				`<li><strong>${__("Roles removed from {0}", [esc(m.profile)])}:</strong> ${m.remove
					.map(esc)
					.join(", ")}</li>`
			);
		});
		if (d.perm_remove.length) {
			rows.push(
				`<li><strong>${__("Permissions removed")} (${d.perm_remove.length}):</strong><br>${rpp_perm_lines(
					d.perm_remove
				)}</li>`
			);
		}
		parts.push(
			`<div style="border:1px solid #e24c4c;border-radius:6px;padding:8px 12px;margin-bottom:10px;background:#fff5f5;">
				<div style="color:#c0392b;font-weight:600;margin-bottom:4px;">⚠ ${__("Will be deleted")}</div>
				<ul style="margin:0 0 0 16px;">${rows.join("")}</ul>
			</div>`
		);
	}

	// New profiles that will be created from the sheet.
	newProfiles.forEach((m) => {
		parts.push(
			`<p><strong>${__("New profile {0} will be created", [esc(m.profile)])}</strong>${
				m.final.length ? ` ${__("with roles")}: ${m.final.map(esc).join(", ")}` : ""
			}</p>`
		);
	});

	// Additions / updates.
	memberAdditions.forEach((m) => {
		parts.push(
			`<p><strong>${__("Roles added to {0}", [esc(m.profile)])}:</strong> ${m.add.map(esc).join(", ")}</p>`
		);
	});
	if (d.perm_add.length) {
		parts.push(
			`<p><strong>${__("Permissions added")} (${d.perm_add.length}):</strong><br>${rpp_perm_lines(d.perm_add)}</p>`
		);
	}
	if (d.perm_update.length) {
		parts.push(
			`<p><strong>${__("Permissions changed")} (${d.perm_update.length}):</strong><br>${rpp_perm_lines(
				d.perm_update,
				true
			)}</p>`
		);
	}

	// Global blast-radius warning.
	const impactRows = Object.keys(d.impact || {})
		.filter((role) => d.impact[role].other_profiles || d.impact[role].users)
		.map((role) => {
			const i = d.impact[role];
			return `<li>${esc(role)} — ${__("also used by {0} other profile(s) and {1} user(s)", [
				i.other_profiles,
				i.users,
			])}</li>`;
		});
	if (impactRows.length) {
		parts.push(
			`<div style="border:1px solid #f0ad4e;border-radius:6px;padding:8px 12px;margin-top:10px;background:#fffaf0;">
				<div style="color:#b9770e;font-weight:600;margin-bottom:4px;">${__(
					"Heads up — these role changes are global"
				)}</div>
				<ul style="margin:0 0 0 16px;">${impactRows.join("")}</ul>
			</div>`
		);
	}

	return parts.join("");
}

function rpp_perm_lines(items, withChanges) {
	const esc = frappe.utils.escape_html;
	return items
		.map((it) => {
			const owner = it.if_owner ? " (if owner)" : "";
			const chg = withChanges && it.changes && it.changes.length ? ` — ${it.changes.join(", ")}` : "";
			return `&nbsp;&nbsp;• ${esc(it.role)} → ${esc(it.doctype)} [L${it.level}${owner}]${esc(chg)}`;
		})
		.join("<br>");
}
