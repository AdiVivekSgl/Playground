// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const WPS_CREATE_PLAN_METHOD =
	"playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager.create_production_plan_from_snapshot";
const WPS_MR_EXCEL_METHOD =
	"playground.playground.production_plan_mr_excel.download_fgsrm_mr_excel";

function wps_download_mr_excel(pp_name) {
	const a = document.createElement("a");
	a.href = `/api/method/${WPS_MR_EXCEL_METHOD}?name=${encodeURIComponent(pp_name)}`;
	a.target = "_blank";
	a.rel = "noopener";
	document.body.appendChild(a);
	a.click();
	a.remove();
}

frappe.ui.form.on("Weekly Planning Snapshot", {
	refresh(frm) {
		wps_bind_consolidated_input(frm);
		wps_apply_view(frm);

		if (frm.doc.docstatus === 0 && (frm.doc.items || []).length) {
			frm.add_custom_button(__("Create Prodn Plan"), () => {
				frappe.confirm(
					__("Create a draft Production Plan from this snapshot's itemwise Committed Prodn? It builds the full nested plan chain and raw materials, then downloads the Production Plan workbook. Save any edits first."),
					() => {
						frappe.call({
							method: WPS_CREATE_PLAN_METHOD,
							args: { snapshot: frm.doc.name },
							freeze: true,
							freeze_message: __("Creating Production Plan…"),
							callback(r) {
								const m = r.message;
								if (!m || !m.name) return;
								if (m.handed_off) {
									frappe.show_alert({
										message: __("Production Plan {0}: {1} item(s), {2} raw material line(s), full chain built. Downloading workbook…", [
											m.name,
											m.items,
											m.raw_materials,
										]),
										indicator: "green",
									});
									wps_download_mr_excel(m.name);
								} else {
									frappe.show_alert({
										message: __("Draft Production Plan {0} created with {1} item(s). Open it and click “Create Full Chain”, then download the workbook.", [
											m.name,
											m.items,
										]),
										indicator: "blue",
									});
									frappe.set_route("Form", "Production Plan", m.name);
								}
							},
						});
					}
				);
			});
		}
	},

	view_mode(frm) {
		wps_apply_view(frm);
	},

	hide_zero_rows(frm) {
		wps_apply_view(frm);
	},
});

frappe.ui.form.on("Weekly Planning Snapshot Item", {
	// Editing Committed Prodn in the Detailed grid keeps the Consolidated view in
	// sync (and vice-versa - the child table is the single source of truth).
	committed_prodn(frm) {
		if (frm._wps_allocating) return;
		wps_render_consolidated(frm);
		if (frm.doc.view_mode === "Detailed") wps_apply_hide_zero_detailed(frm);
	},
	items_remove(frm) {
		wps_render_consolidated(frm);
	},
});

// ─────────────────────────────────────────────────────────────────────────
// View switching (never changes data - only what's shown/edited)
// ─────────────────────────────────────────────────────────────────────────
function wps_apply_view(frm) {
	const consolidated = frm.doc.view_mode === "Consolidated";
	frm.toggle_display("items", !consolidated);
	frm.toggle_display("consolidated_requirement_html", consolidated);
	if (consolidated) {
		wps_render_consolidated(frm);
	} else {
		wps_apply_hide_zero_detailed(frm);
	}
}

// ─────────────────────────────────────────────────────────────────────────
// Consolidated view: one editable row per Item Code
// ─────────────────────────────────────────────────────────────────────────
function wps_consolidate(frm) {
	const by = {};
	const order = [];
	(frm.doc.items || []).forEach((d) => {
		if (!(d.item_code in by)) {
			by[d.item_code] = { name: d.item_name || "", free: flt(d.item_free_stock), sug: 0, com: 0 };
			order.push(d.item_code);
		}
		by[d.item_code].sug += flt(d.suggested_prodn);
		by[d.item_code].com += flt(d.committed_prodn);
	});
	return { by, order };
}

function wps_render_consolidated(frm) {
	const field = frm.get_field("consolidated_requirement_html");
	if (!field) return;
	const editable = frm.doc.docstatus === 0;
	const hideZero = frm.doc.hide_zero_rows;
	const { by, order } = wps_consolidate(frm);

	const body = order
		.filter((ic) => !hideZero || by[ic].com > 0)
		.map((ic) => {
			const r = by[ic];
			const committedCell = editable
				? `<input type="number" step="any" data-wps-item="${frappe.utils.escape_html(ic)}" ` +
				  `value="${r.com}" style="width:110px;text-align:right;" class="form-control input-sm" />`
				: `<b>${format_number(r.com)}</b>`;
			return (
				`<tr><td>${frappe.utils.escape_html(ic)}</td><td>${frappe.utils.escape_html(r.name)}</td>` +
				`<td style="text-align:right">${format_number(r.free)}</td>` +
				`<td style="text-align:right">${format_number(r.sug)}</td>` +
				`<td style="text-align:right">${committedCell}</td></tr>`
			);
		})
		.join("");
	const totalCom = order.reduce((s, ic) => s + by[ic].com, 0);

	field.$wrapper.html(
		`<table class="table table-bordered" style="font-size:12px;"><thead><tr>` +
			`<th>${__("Item")}</th><th>${__("Item Name")}</th><th style="text-align:right">${__("Item Free Stock")}</th>` +
			`<th style="text-align:right">${__("Total Suggested")}</th><th style="text-align:right">${__("Committed Prodn")}</th>` +
			`</tr></thead><tbody>${body}</tbody>` +
			`<tfoot><tr><th colspan="4" style="text-align:right">${__("Total Committed Prodn")}</th>` +
			`<th style="text-align:right">${format_number(totalCom)}</th></tr></tfoot></table>` +
			(editable ? `<p class="text-muted small">${__("Editing an item's Committed Prodn allocates it back to that item's Sales Order lines, earliest Sales Order Date first.")}</p>` : "")
	);
}

// Delegated handler (bound once) - survives re-renders of the table.
function wps_bind_consolidated_input(frm) {
	const field = frm.get_field("consolidated_requirement_html");
	if (!field || field._wps_bound) return;
	field._wps_bound = true;
	field.$wrapper.on("change", "input[data-wps-item]", function () {
		const item = $(this).attr("data-wps-item");
		let total = flt($(this).val());
		if (total < 0) total = 0;
		wps_allocate(frm, item, total);
		wps_render_consolidated(frm);
	});
}

// Allocate an item's total committed across its SO lines, EARLIEST Sales Order
// Date first: fill each line up to its Suggested Prodn, then any surplus goes to
// the earliest (highest-priority) line.
function wps_allocate(frm, item_code, new_total) {
	const rows = (frm.doc.items || [])
		.filter((d) => d.item_code === item_code)
		.sort((a, b) => (a.so_date || "9999-12-31").localeCompare(b.so_date || "9999-12-31"));
	if (!rows.length) return;

	let remaining = flt(new_total);
	const alloc = rows.map((d) => {
		const give = Math.min(flt(d.suggested_prodn), remaining);
		remaining -= give;
		return give;
	});
	if (remaining > 0.0001) alloc[0] += remaining; // surplus -> earliest SO

	frm._wps_allocating = true;
	rows.forEach((d, i) => frappe.model.set_value(d.doctype, d.name, "committed_prodn", flt(alloc[i])));
	frm._wps_allocating = false;
	frm.dirty();
}

// ─────────────────────────────────────────────────────────────────────────
// Hide Zero Rows - Detailed grid (best-effort client-side row hide)
// ─────────────────────────────────────────────────────────────────────────
function wps_apply_hide_zero_detailed(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) return;
	const hide = frm.doc.hide_zero_rows;
	(grid.grid_rows || []).forEach((gr) => {
		if (!gr || !gr.wrapper) return;
		const show = !hide || flt(gr.doc.committed_prodn) > 0;
		$(gr.wrapper).toggle(show);
	});
}
