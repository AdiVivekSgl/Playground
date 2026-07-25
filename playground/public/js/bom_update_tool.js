// Adds a "Replace Component Item" action to ERPNext's native BOM Update Tool.
//
// ERPNext's built-in "Replace BOM" swaps one whole sub-assembly BOM for another.
// This fills the gap: swap a single component *item* (a BOM Item row) for a
// different item across every BOM that uses it - and it works whether the
// replacement is a purchased raw material or a manufactured sub-assembly.
//
// Server side: playground/playground/bom_component_replace.py
//   - find_affected_boms(old_item)      preview which BOMs contain the item
//   - replace_component_item(old, new, boms)  do the swap on the ticked BOMs

const API = "playground.playground.bom_component_replace";

// Renders the ticked-BOM picker into the dialog's HTML field. Active/default
// BOMs are surfaced first (that ordering comes from the server query).
function render_affected_boms(dialog, boms) {
	const $wrap = dialog.fields_dict.affected_html.$wrapper;

	if (!boms.length) {
		$wrap.html(
			`<p class="text-muted">${__("No active or draft BOMs contain that item.")}</p>`
		);
		toggle_replace_button(dialog, false);
		return;
	}

	const rows_html = boms
		.map((b) => {
			const flags = [];
			if (b.docstatus === 0) flags.push(__("Draft"));
			if (b.is_active) flags.push(__("Active"));
			if (b.is_default) flags.push(__("Default"));
			return `
				<tr>
					<td style="width:32px;text-align:center;">
						<input type="checkbox" data-bom="${frappe.utils.escape_html(b.bom)}" checked />
					</td>
					<td>${frappe.utils.escape_html(b.bom)}</td>
					<td>${frappe.utils.escape_html(b.bom_item || "")}${
						b.item_name ? " — " + frappe.utils.escape_html(b.item_name) : ""
					}</td>
					<td style="text-align:right;">${b.occurrences}</td>
					<td>${frappe.utils.escape_html(flags.join(", "))}</td>
				</tr>`;
		})
		.join("");

	const html = `
		<p>${__("Tick the BOMs to update. Submitted BOMs are edited in place (same name); their costs are recomputed.")}</p>
		<div style="margin-bottom:6px;">
			<a class="small" data-action="select-all">${__("Select all")}</a> ·
			<a class="small" data-action="select-none">${__("Select none")}</a>
		</div>
		<table class="table table-bordered" style="font-size:12px;">
			<thead>
				<tr>
					<th></th><th>${__("BOM")}</th><th>${__("For Item")}</th>
					<th style="text-align:right;">${__("Rows")}</th><th>${__("Status")}</th>
				</tr>
			</thead>
			<tbody>${rows_html}</tbody>
		</table>`;

	$wrap.html(html);

	$wrap.find('[data-action="select-all"]').on("click", () =>
		$wrap.find('input[type="checkbox"]').prop("checked", true)
	);
	$wrap.find('[data-action="select-none"]').on("click", () =>
		$wrap.find('input[type="checkbox"]').prop("checked", false)
	);

	toggle_replace_button(dialog, true);
}

// The "Replace in Selected BOMs" button lives in the footer and only appears
// once a preview has produced at least one candidate BOM.
function toggle_replace_button(dialog, show) {
	dialog._replace_btn.toggle(!!show);
}

function selected_boms(dialog) {
	return Array.from(
		dialog.fields_dict.affected_html.$wrapper[0].querySelectorAll(
			'input[type="checkbox"]:checked'
		)
	).map((el) => el.getAttribute("data-bom"));
}

function run_replace(dialog) {
	const old_item = dialog.get_value("old_item");
	const new_item = dialog.get_value("new_item");
	const boms = selected_boms(dialog);

	if (!old_item || !new_item) {
		frappe.msgprint(__("Enter both the current item and the replacement item."));
		return;
	}
	if (old_item === new_item) {
		frappe.msgprint(__("The replacement item is the same as the current item."));
		return;
	}
	if (!boms.length) {
		frappe.msgprint(__("Tick at least one BOM to update."));
		return;
	}

	frappe.confirm(
		__(
			"Replace <b>{0}</b> with <b>{1}</b> on {2} BOM(s)? Submitted BOMs are edited in place and this can't be undone.",
			[
				frappe.utils.escape_html(old_item),
				frappe.utils.escape_html(new_item),
				boms.length,
			]
		),
		() => {
			frappe.call({
				method: API + ".replace_component_item",
				args: { old_item, new_item, boms: JSON.stringify(boms) },
				freeze: true,
				freeze_message: __("Replacing component item…"),
				callback(r) {
					const m = r.message || {};
					const updated = m.updated || [];
					const skipped = m.skipped || [];
					const failed = m.failed || {};
					const failed_names = Object.keys(failed);

					frappe.show_alert({
						message: __("Updated {0} BOM(s); {1} skipped, {2} failed.", [
							updated.length,
							skipped.length,
							failed_names.length,
						]),
						indicator:
							failed_names.length || skipped.length ? "orange" : "green",
					});

					const parts = [];
					if (updated.length) {
						parts.push(
							`<b>${__("Updated")}:</b><br>` +
								updated.map((n) => frappe.utils.escape_html(n)).join("<br>")
						);
					}
					if (skipped.length) {
						parts.push(
							`<b>${__("Skipped")}:</b><br>` +
								skipped
									.map(
										(s) =>
											`${frappe.utils.escape_html(s.name)}: ${frappe.utils.escape_html(s.reason)}`
									)
									.join("<br>")
						);
					}
					if (failed_names.length) {
						parts.push(
							`<b>${__("Failed")}:</b><br>` +
								failed_names
									.map(
										(n) =>
											`${frappe.utils.escape_html(n)}: ${frappe.utils.escape_html(failed[n])}`
									)
									.join("<br>")
						);
					}

					frappe.msgprint({
						title: __("Replace Component Item — Result"),
						message: parts.join("<hr>") || __("Nothing changed."),
						indicator: failed_names.length ? "red" : "green",
					});

					if (updated.length) {
						dialog.hide();
						frappe.msgprint({
							title: __("Recompute parent BOM costs"),
							message: __(
								"Component swapped. Parent BOMs that <i>use</i> an edited BOM are not re-costed automatically — run <b>Update Cost</b> on this tool to cascade the new cost upward."
							),
							indicator: "blue",
						});
					}
				},
			});
		}
	);
}

function open_replace_component_dialog() {
	const dialog = new frappe.ui.Dialog({
		title: __("Replace Component Item Across BOMs"),
		size: "large",
		fields: [
			{
				fieldname: "old_item",
				fieldtype: "Link",
				label: __("Current Item (to replace)"),
				options: "Item",
				reqd: 1,
			},
			{
				fieldname: "new_item",
				fieldtype: "Link",
				label: __("Replacement Item"),
				options: "Item",
				reqd: 1,
			},
			{ fieldname: "affected_html", fieldtype: "HTML" },
		],
		primary_action_label: __("Find Affected BOMs"),
		primary_action() {
			const old_item = dialog.get_value("old_item");
			if (!old_item) {
				frappe.msgprint(__("Select the item to be replaced."));
				return;
			}
			frappe.call({
				method: API + ".find_affected_boms",
				args: { old_item },
				freeze: true,
				freeze_message: __("Finding BOMs…"),
				callback(r) {
					render_affected_boms(dialog, r.message || []);
				},
			});
		},
	});

	// Second footer action: hidden until a preview finds candidate BOMs.
	// Appended directly to the footer (frappe.ui.Dialog only exposes one primary
	// + one secondary action) - same approach as kit_content_mapping.js.
	dialog._replace_btn = $(
		`<button class="btn btn-danger btn-sm">${__("Replace in Selected BOMs")}</button>`
	)
		.on("click", () => run_replace(dialog))
		.hide();
	dialog.$wrapper.find(".modal-footer").prepend(dialog._replace_btn);

	// Re-running the preview after changing either item is expected, so clear
	// stale results when the inputs change.
	dialog.fields_dict.old_item.$input.on("change", () => toggle_replace_button(dialog, false));
	dialog.fields_dict.new_item.$input.on("change", () => toggle_replace_button(dialog, false));

	dialog.show();
}

frappe.ui.form.on("BOM Update Tool", {
	refresh(frm) {
		frm.add_custom_button(__("Replace Component Item"), () =>
			open_replace_component_dialog()
		);
	},
});
