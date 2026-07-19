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
		wps_render_consolidated(frm);
		wps_apply_only_suggested(frm);

		// Create Prodn Plan from the DRAFT snapshot's itemwise Committed Prodn.
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

	only_suggested(frm) {
		wps_apply_only_suggested(frm);
	},
});

frappe.ui.form.on("Weekly Planning Snapshot Item", {
	// Committed Prodn edits don't change Suggested Prodn - just refresh the
	// consolidated summary. (Server re-renders authoritatively on save.)
	committed_prodn(frm) {
		wps_render_consolidated(frm);
	},
	items_remove(frm) {
		wps_render_consolidated(frm);
	},
});

// Per-item summary rendered client-side for live feedback (mirrors the server's
// _render_consolidated).
function wps_render_consolidated(frm) {
	const field = frm.get_field("consolidated_requirement_html");
	if (!field) return;
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
	const rows = order
		.map(
			(ic) =>
				`<tr><td>${frappe.utils.escape_html(ic)}</td><td>${frappe.utils.escape_html(by[ic].name)}</td>` +
				`<td style="text-align:right">${format_number(by[ic].free)}</td>` +
				`<td style="text-align:right">${format_number(by[ic].sug)}</td>` +
				`<td style="text-align:right"><b>${format_number(by[ic].com)}</b></td></tr>`
		)
		.join("");
	const totalCom = order.reduce((s, ic) => s + by[ic].com, 0);
	field.$wrapper.html(
		`<table class="table table-bordered" style="font-size:12px;"><thead><tr>` +
			`<th>${__("Item")}</th><th>${__("Item Name")}</th><th style="text-align:right">${__("Item Free Stock")}</th>` +
			`<th style="text-align:right">${__("Total Suggested")}</th><th style="text-align:right">${__("Total Committed")}</th>` +
			`</tr></thead><tbody>${rows}</tbody>` +
			`<tfoot><tr><th colspan="4" style="text-align:right">${__("Total Committed Prodn")}</th>` +
			`<th style="text-align:right">${format_number(totalCom)}</th></tr></tfoot></table>`
	);
}

function wps_apply_only_suggested(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid) return;
	const only = frm.doc.only_suggested;
	(grid.grid_rows || []).forEach((gr) => {
		if (!gr || !gr.wrapper) return;
		const show = !only || flt(gr.doc.suggested_prodn) > 0;
		$(gr.wrapper).toggle(show);
	});
}
