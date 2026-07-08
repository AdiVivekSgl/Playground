// Subassembly rows that have at least one mapped child row but no Item Code
// yet — generating BOMs would eventually fail deep inside BOM building for
// these, so we catch it up front instead.
function get_subassembly_rows_missing_item_code(frm) {
	const rows = (frm.doc.mapping_items || []).slice().sort((a, b) => a.idx - b.idx);
	const missing = [];
	rows.forEach((row, i) => {
		if (row.framework_node_type !== "Subassembly" || row.item_code) return;
		let has_child = false;
		for (let j = i + 1; j < rows.length; j++) {
			if (rows[j].indent_level <= row.indent_level) break;
			if (rows[j].indent_level === row.indent_level + 1) {
				has_child = true;
				break;
			}
		}
		if (has_child) missing.push(row);
	});
	return missing;
}

// Prompts for an Item Code on every Subassembly row that has mapped children
// but none yet, before letting Generate BOMs proceed. Resolves immediately
// (no prompt shown) if every such row already has an Item Code.
function ensure_subassembly_item_codes(frm) {
	const missing = get_subassembly_rows_missing_item_code(frm);
	if (!missing.length) return Promise.resolve();

	return new Promise((resolve) => {
		frappe.prompt(
			missing.map((row) => ({
				fieldname: row.name,
				label: __('Item Code for "{0}"', [row.node_name || row.name]),
				fieldtype: "Link",
				options: "Item",
				reqd: 1,
			})),
			(values) => {
				missing.forEach((row) => {
					frappe.model.set_value(row.doctype, row.name, "item_code", values[row.name]);
				});
				frm.call({ method: "save_relaxed", doc: frm.doc }).then(() => resolve());
			},
			__("Enter Item Codes for Subassemblies with Mapped Children"),
			__("Continue")
		);
	});
}

// Renders the mapping-vs-BOM comparison table into the dialog's HTML field.
function render_bom_comparison(dialog, data) {
	const mapping_children = data.mapping_children || [];
	const bom_items = data.bom_items || [];
	const bom_map = {};
	bom_items.forEach((b) => (bom_map[b.item_code] = b));
	const mapping_codes = new Set(mapping_children.map((m) => m.item_code));

	const rows_html = [];
	mapping_children.forEach((m) => {
		const b = bom_map[m.item_code];
		const match = b && Math.abs(flt(b.qty) - flt(m.qty)) < 0.0001;
		rows_html.push(`
			<tr style="${b ? "" : "background:#fdecea;"}">
				<td>${frappe.utils.escape_html(m.item_code)}</td>
				<td style="text-align:right">${m.qty}</td>
				<td style="text-align:right">${b ? b.qty : "—"}</td>
				<td>${b ? (match ? "Match" : "Qty differs") : "Only in mapping"}</td>
			</tr>`);
	});
	bom_items.forEach((b) => {
		if (!mapping_codes.has(b.item_code)) {
			rows_html.push(`
				<tr style="background:#fff8e1;">
					<td>${frappe.utils.escape_html(b.item_code)}</td>
					<td style="text-align:right">—</td>
					<td style="text-align:right">${b.qty}</td>
					<td>Only in BOM</td>
				</tr>`);
		}
	});

	const html = `
		<table class="table table-bordered" style="font-size:12px;">
			<thead>
				<tr><th>Item Code</th><th style="text-align:right">Mapping Qty</th><th style="text-align:right">BOM Qty</th><th>Status</th></tr>
			</thead>
			<tbody>${rows_html.join("") || '<tr><td colspan="4">No components on either side.</td></tr>'}</tbody>
		</table>`;

	dialog.fields_dict.comparison_html.$wrapper.html(html);
}

// Compare BOM dialog for a Subassembly row: shows the mapping's typed
// children next to an existing BOM's items, then lets the user resolve the
// row's Treatment as Subassembly Existing (replacing the mapped children
// with the BOM's own items, after a warning), Subassembly New, or Passthrough.
function open_compare_bom_dialog(frm, row) {
	if (!row.item_code) {
		frappe.msgprint(__("Set an Item Code on this row before comparing against a BOM."));
		return;
	}

	const dialog = new frappe.ui.Dialog({
		title: __('Compare BOM — "{0}"', [row.node_name || row.item_code]),
		size: "large",
		fields: [
			{
				fieldname: "bom_name",
				fieldtype: "Link",
				label: __("Compare Against BOM"),
				options: "BOM",
				default: row.bom || "",
				get_query: () => ({
					filters: { item: row.item_code, is_active: 1, docstatus: 1 },
				}),
			},
			{ fieldname: "comparison_html", fieldtype: "HTML" },
		],
		primary_action_label: __("Compare"),
		primary_action: (values) => {
			if (!values.bom_name) {
				frappe.msgprint(__("Select a BOM to compare against."));
				return;
			}
			frm.call("compare_bom_children", { row_name: row.name, bom_name: values.bom_name }).then((r) => {
				render_bom_comparison(dialog, r.message || {});
			});
		},
	});

	// Three mutually exclusive outcomes — frappe.ui.Dialog only supports one
	// primary + one secondary action, so these are appended directly.
	const footer = dialog.$wrapper.find(".modal-footer");

	const make_btn = (label, cls, handler) => {
		const $btn = $(`<button class="btn ${cls} btn-sm">${label}</button>`).on("click", handler);
		footer.prepend($btn);
		return $btn;
	};

	make_btn(__("Passthrough"), "btn-default", () => {
		frappe.model.set_value(row.doctype, row.name, "treatment", "Passthrough");
		frappe.model.set_value(row.doctype, row.name, "item_code", "");
		frappe.model.set_value(row.doctype, row.name, "bom", "");
		dialog.hide();
		frm.refresh_field("mapping_items");
	});

	make_btn(__("Use New"), "btn-default", () => {
		frappe.model.set_value(row.doctype, row.name, "treatment", "Subassembly New");
		frappe.model.set_value(row.doctype, row.name, "bom", "");
		dialog.hide();
		frm.refresh_field("mapping_items");
	});

	make_btn(__("Use Existing"), "btn-danger", () => {
		const bom_name = dialog.get_value("bom_name");
		if (!bom_name) {
			frappe.msgprint(__("Select a BOM first."));
			return;
		}
		frappe.confirm(
			__(
				"This will REPLACE this row's current child items in the mapping with {0}'s own items. This cannot be undone. Continue?",
				[bom_name]
			),
			() => {
				frm.call("use_existing_bom_for_row", { row_name: row.name, bom_name: bom_name }).then(() => {
					dialog.hide();
					frappe.show_alert({
						message: __("Children replaced from {0}.", [bom_name]),
						indicator: "green",
					});
					frm.reload_doc();
				});
			}
		);
	});

	dialog.show();

	// Auto-run the comparison once immediately if a BOM is already known.
	if (row.bom) {
		frm.call("compare_bom_children", { row_name: row.name, bom_name: row.bom }).then((r) => {
			render_bom_comparison(dialog, r.message || {});
		});
	}
}

function show_explosion_dialog(title, lines) {
	if (!lines.length) {
		frappe.msgprint(
			__("Nothing to preview yet — map at least one top-level row with an item code first.")
		);
		return;
	}
	const rows_html = lines
		.map(
			(l) => `
			<tr>
				<td>${frappe.utils.escape_html(l.node || "")}</td>
				<td>${frappe.utils.escape_html(l.item_code || "")}</td>
				<td style="text-align:right">${l.qty}</td>
				<td>${frappe.utils.escape_html(l.uom || "")}</td>
			</tr>`
		)
		.join("");
	const html = `
		<table class="table table-bordered" style="font-size:12px;">
			<thead>
				<tr><th>Node</th><th>Item Code</th><th style="text-align:right">Qty</th><th>UOM</th></tr>
			</thead>
			<tbody>${rows_html}</tbody>
		</table>`;
	new frappe.ui.Dialog({
		title: title,
		size: "large",
		fields: [{ fieldtype: "HTML", options: html }],
	}).show();
}

// Visually convey the mapping tree directly in the grid's Node column:
//   • indent the text by indent_level (level 1 = flush left)
//   • bold Subassembly rows, italicise Passthrough rows
// Implemented as a grid cell formatter on node_name so it survives grid
// refreshes without touching the stored data. Read-only display only — the
// underlying Link value is unchanged.
function style_node_column(frm) {
	const grid = frm.fields_dict.mapping_items && frm.fields_dict.mapping_items.grid;
	if (!grid) return;
	const df = grid.get_docfield("node_name");
	if (!df) return;

	df.formatter = function (value, field, options, doc) {
		const text = frappe.utils.escape_html(value || "");
		if (!doc) return text;

		const level = parseInt(doc.indent_level, 10) || 1;
		const pad = Math.max(0, level - 1) * 16; // 16px per indent level

		let style = `padding-left:${pad}px;`;
		if (doc.framework_node_type === "Subassembly") {
			style += "font-weight:600;";
		} else if (doc.framework_node_type === "Passthrough") {
			style += "font-style:italic;";
		}
		return `<span style="${style}">${text}</span>`;
	};

	grid.refresh();
}

// Every BOM this mapping points at, as {bom, label} - FG BOM (L1 default), the
// generated alternate-level BOMs, and any BOM linked on a mapping row. Mirrors
// the server-side _linked_boms(); de-duplicated, FG BOM first.
function collect_linked_boms(frm) {
	const seen = new Set();
	const out = [];
	const add = (bom, label) => {
		if (bom && !seen.has(bom)) {
			seen.add(bom);
			out.push({ bom, label });
		}
	};
	add(frm.doc.fg_bom, __("FG BOM (L1, default)"));
	(frm.doc.generated_boms || []).forEach((d) => add(d.bom, d.level_label || __("Alternate level")));
	(frm.doc.mapping_items || []).forEach((r) => {
		if (r.bom) add(r.bom, __("On row: {0}", [r.node_name || r.item_code || r.bom]));
	});
	return out;
}

function open_delete_boms_dialog(frm) {
	const boms = collect_linked_boms(frm);
	if (!boms.length) {
		frappe.msgprint(__("This mapping isn't linked to any BOMs."));
		return;
	}

	const rows_html = boms
		.map(
			(b, i) => `
			<tr>
				<td><input type="checkbox" data-bom="${frappe.utils.escape_html(b.bom)}" /></td>
				<td>${frappe.utils.escape_html(b.bom)}</td>
				<td>${frappe.utils.escape_html(b.label)}</td>
			</tr>`
		)
		.join("");

	const html = `
		<p>${__("Tick the BOMs to delete. They'll be unlinked from this Kit Content Mapping and then cancelled &amp; deleted. The mapping itself is kept.")}</p>
		<table class="table table-bordered" style="font-size:12px;">
			<thead><tr><th style="width:32px;"></th><th>${__("BOM")}</th><th>${__("Linked as")}</th></tr></thead>
			<tbody>${rows_html}</tbody>
		</table>`;

	const dialog = new frappe.ui.Dialog({
		title: __("Delete Generated BOMs"),
		size: "large",
		fields: [{ fieldtype: "HTML", options: html }],
		primary_action_label: __("Delete Selected"),
		primary_action() {
			const selected = Array.from(
				dialog.$wrapper[0].querySelectorAll('input[type="checkbox"]:checked')
			).map((el) => el.getAttribute("data-bom"));

			if (!selected.length) {
				frappe.msgprint(__("Tick at least one BOM to delete."));
				return;
			}

			frappe.confirm(
				__(
					"Permanently cancel &amp; delete {0} BOM(s)? This can't be undone. Any BOM that's a default without a replacement, used in another active BOM, or tied to stock/Work Order transactions will be reported as skipped.",
					[selected.length]
				),
				() => {
					dialog.hide();
					frm.call({
						method: "delete_generated_boms",
						doc: frm.doc,
						args: { bom_names: JSON.stringify(selected) },
						freeze: true,
						freeze_message: __("Deleting BOMs…"),
					}).then((r) => {
						const m = r.message || {};
						const failed = m.failed || {};
						const failed_names = Object.keys(failed);
						frappe.show_alert({
							message: __("Deleted {0} BOM(s); {1} skipped.", [
								(m.deleted || []).length,
								failed_names.length,
							]),
							indicator: failed_names.length ? "orange" : "green",
						});
						if (failed_names.length) {
							frappe.msgprint({
								title: __("Some BOMs couldn't be deleted"),
								message: failed_names
									.map((n) => `<b>${frappe.utils.escape_html(n)}</b>: ${frappe.utils.escape_html(failed[n])}`)
									.join("<br>"),
								indicator: "orange",
							});
						}
						frm.reload_doc();
					});
				}
			);
		},
	});
	dialog.show();
}

frappe.ui.form.on("Kit Content Mapping", {
	refresh(frm) {
		style_node_column(frm);

		// ── Revert to original BOM ─────────────────────────────────────
		if (frm.doc.source_bom) {
			frm.add_custom_button(__("Revert to original BOM"), () => {
				frappe.confirm(
					__(
						"This will clear all mapping rows and reload them flat from the original BOM. Framework selection and node assignments will be lost. Previously generated BOMs are not affected. Proceed?"
					),
					() => {
						frm.call("revert_to_original_bom").then(() => {
							frappe.show_alert({
								message: __("Reverted — BOM items reloaded flat. Select a framework and run Apply Node Structure to re-structure."),
								indicator: "blue",
							});
							frm.reload_doc();
						});
					}
				);
			});
		}

		// ── Apply node structure ─────────────────────────────────────────
		if (frm.doc.kit_content_framework && frm.doc.mapping_items && frm.doc.mapping_items.length) {
			frm.add_custom_button(__("Apply node structure"), () => {
				frm.call("apply_node_structure").then((r) => {
					const res = r.message || {};
					const msg = __(
						"{0} node(s) inserted from framework, {1} row(s) unmatched → moved to bottom.",
						[res.inserted || 0, res.unmatched || 0]
					);
					frappe.show_alert({ message: msg, indicator: res.unmatched ? "orange" : "green" });
					frm.reload_doc();
				});
			});
		}

		// ── Node name filter: restrict to nodes in the selected framework ─
		frm.set_query("node_name", "mapping_items", () => {
			if (!frm._framework_node_names || !frm._framework_node_names.length) return {};
			return { filters: [["name", "in", frm._framework_node_names]] };
		});

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
			ensure_subassembly_item_codes(frm).then(() => {
				frm.call({ method: "generate_pending_boms", doc: frm.doc }).then((r) => {
					const created = r.message || [];
					if (created.length) {
						frappe.show_alert({
							message: __(
								"Created {0} BOM(s) — including the FG Item's full level set (L1 default, plus alternates) where applicable.",
								[created.length]
							),
							indicator: "green",
						});
						frm.reload_doc();
					} else {
						frappe.show_alert({
							message: __(
								"Nothing to generate — the FG Item's BOM set already exists and every Subassembly New row already has one too, or none are mapped yet."
							),
							indicator: "blue",
						});
					}
				});
			});
		});

		frm.add_custom_button(__("Preview Fully Exploded FG BOM"), () => {
			frm.call({ method: "preview_fully_exploded_fg_bom", doc: frm.doc }).then((r) => {
				show_explosion_dialog(
					__("Fully Exploded FG BOM — Preview (nothing saved)"),
					r.message || []
				);
			});
		});

		frm.add_custom_button(__("Preview Custom Exploded BOM"), () => {
			frm.call({ method: "preview_custom_exploded_fg_bom", doc: frm.doc }).then((r) => {
				show_explosion_dialog(
					__("Custom Exploded FG BOM — Preview (nothing saved)"),
					r.message || []
				);
			});
		});

		frm.add_custom_button(__("Generate Custom Exploded BOM"), () => {
			frm.call({ method: "generate_custom_exploded_bom", doc: frm.doc }).then((r) => {
				if (r.message) {
					frappe.show_alert({
						message: __("Created Custom Exploded BOM: {0}", [r.message]),
						indicator: "green",
					});
					frm.reload_doc();
				}
			});
		});

		// ── Delete generated / linked BOMs (without deleting this mapping) ──
		if (!frm.is_new() && collect_linked_boms(frm).length) {
			frm.add_custom_button(
				__("Delete Generated BOMs"),
				() => open_delete_boms_dialog(frm),
				__("BOMs")
			);
		}
	},

	kit_content_framework(frm) {
		if (!frm.doc.kit_content_framework) return;
		frappe.db.get_doc("Kit Content Framework", frm.doc.kit_content_framework).then((framework) => {
			// Cache framework node names so the node_name Link query can filter.
			frm._framework_node_names = (framework.items || []).map((fi) => fi.node_name);

			// If the mapping already has rows (e.g. loaded flat from a source
			// BOM via the Work Order "Update BOM" button), do NOT wipe them.
			// Reconciling those existing rows against this framework is exactly
			// what "Apply node structure" does — it preserves the items and
			// moves any that don't match a framework node under "Other".
			// Clobbering the table here would delete the BOM items the user is
			// trying to re-structure. Just record the framework and save so the
			// "Apply node structure" button appears.
			if (frm.doc.mapping_items && frm.doc.mapping_items.length) {
				frm.call({ method: "save_relaxed", doc: frm.doc }).then(() => {
					frappe.show_alert({
						message: __(
							"Framework \"{0}\" selected. Click \"Apply node structure\" to reorganise the existing rows under it.",
							[framework.framework_name]
						),
						indicator: "blue",
					});
					frm.reload_doc();
				});
				return;
			}

			// Empty mapping → seed it straight from the framework template.
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
						? "Subassembly New"
						: "";
				// Copy keep_aggregated from the framework row as a starting default
				// — the user can override it freely in the mapping from here on.
				row.keep_aggregated = fi.keep_aggregated || 0;
			});
			frm.refresh_field("mapping_items");
			frappe.show_alert({
				message: __("Loaded {0} node(s) from \"{1}\".", [
					(framework.items || []).length,
					framework.framework_name,
				]),
				indicator: "blue",
			});

			// Rows just loaded are mostly blank (item_code, bom) — a normal
			// save would fail mandatory validation on nearly every row. This
			// relaxed save just gets the document a real name immediately;
			// the ordinary Save button still enforces every mandatory field
			// the regular way once the user actually fills the rows in.
			frm.call({ method: "save_relaxed", doc: frm.doc }).then((r) => {
				if (r.message && r.message !== frm.doc.name) {
					// Navigate explicitly rather than frm.reload_doc() — we've
					// already hit a case where reloading right after a
					// same-request rename used a name the form hadn't
					// picked up yet. A route change always loads correctly.
					frappe.set_route("Form", "Kit Content Mapping", r.message);
				}
			});
		});
	},
});

frappe.ui.form.on("Kit Content Mapping Item", {
	compare_bom(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		open_compare_bom_dialog(frm, row);
	},

	unlock_components(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.unlock_components) return;
		// Unlock this row's own "Other" children (the rows added when its BOM
		// was selected) so Type / Treatment / Item Code / Qty become editable.
		// This checkbox locks itself once checked (read_only_depends_on on the
		// field) — there's no path back to the locked state.
		(frm.doc.mapping_items || []).forEach((child) => {
			if (child.is_framework_extra && child.bom_source_row === row.name) {
				frappe.model.set_value(child.doctype, child.name, "is_editable", 1);
			}
		});
		frm.refresh_field("mapping_items");
	},

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
		if (!row.bom) return;
		// Ordinary extra ("Other") rows never get a bom picked on them directly
		// — but once unlocked via the parent's "Allow Editing Components"
		// checkbox, a user can retype Treatment to Subassembly Existing/New on
		// one of them, at which point it needs to behave exactly like any
		// other Subassembly Existing row, including triggering its own
		// explode-and-diff recursively.
		if (row.is_framework_extra && !row.is_editable) return;

		// explode_bom_for_row does a self.save() server-side. If this Mapping
		// document has never been saved before, that save is actually the
		// document's first insert — Frappe assigns it its real permanent name
		// at that moment. The form in the browser doesn't know about that
		// rename, so frm.reload_doc() afterward tries to fetch the OLD
		// (now nonexistent) placeholder name and silently does nothing —
		// the rows really do get saved, but the page never shows them.
		// Simplest reliable fix: require an explicit save first.
		if (frm.is_new()) {
			frappe.model.set_value(cdt, cdn, "bom", "");
			frappe.msgprint({
				title: __("Save first"),
				message: __(
					"Please save this Kit Content Mapping before selecting a BOM — picking a BOM on an unsaved document can save it under a new name the form doesn't know about yet, so nothing reloads correctly."
				),
				indicator: "orange",
			});
			return;
		}

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
