// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

frappe.query_reports["Purchase Liability and Cash Flow Forecast"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{ fieldname: "from_date", label: __("Forecast From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Forecast To Date"), fieldtype: "Date" },
		{
			fieldname: "include_overdue",
			label: __("Include Overdue Liabilities"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "consolidated",
			label: __("Consolidated View (by Purchase Order)"),
			fieldtype: "Check",
			default: 0,
		},
		{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link", options: "Supplier" },
		{ fieldname: "supplier_group", label: __("Supplier Group"), fieldtype: "Link", options: "Supplier Group" },
		{
			fieldname: "liability_stage",
			label: __("Liability Stage"),
			fieldtype: "Select",
			options: ["", "Actual", "Received / Unbilled", "Future Commitment"].join("\n"),
		},
		{ fieldname: "purchase_order", label: __("Purchase Order"), fieldtype: "Link", options: "Purchase Order" },
		{ fieldname: "purchase_receipt", label: __("Purchase Receipt"), fieldtype: "Link", options: "Purchase Receipt" },
		{ fieldname: "purchase_invoice", label: __("Purchase Invoice"), fieldtype: "Link", options: "Purchase Invoice" },
		{ fieldname: "item_code", label: __("Item Code"), fieldtype: "Link", options: "Item" },
		{ fieldname: "item_group", label: __("Item Group"), fieldtype: "Link", options: "Item Group" },
		{ fieldname: "cost_center", label: __("Cost Center"), fieldtype: "Link", options: "Cost Center" },
		{ fieldname: "project", label: __("Project"), fieldtype: "Link", options: "Project" },
	],

	// Colour-code the Liability Stage cell (visual aid only — the field value is
	// unchanged): Red overdue Actual, Orange Actual due <=7d, Yellow Actual <=30d,
	// Blue Received/Unbilled, Grey Future Commitment.
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		if (column.fieldname !== "liability_stage" || !data) return formatted;

		const days = cint(data.days_to_due);
		const stage = data.liability_stage;
		let bg = null;
		if (stage === "Actual") {
			if (days < 0) bg = "#fde2e7"; // red — overdue
			else if (days <= 7) bg = "#ffe0b2"; // orange
			else if (days <= 30) bg = "#fff9c4"; // yellow
		} else if (stage === "Received / Unbilled") {
			bg = "#e3f2fd"; // blue
		} else if (stage === "Future Commitment") {
			bg = "#eceff1"; // grey
		}
		if (bg) {
			return `<div style="background-color:${bg};margin:-8px -12px;padding:8px 12px;font-weight:600;">${formatted}</div>`;
		}
		return formatted;
	},

	// Persistent, filter-aware total. Renders a sticky bar under the datatable showing
	// the sum of Forecast Liability across the rows currently VISIBLE — so it updates
	// live as the datatable's inline column filters hide/show rows. Guarded so any
	// datatable-internal change can never break the report render.
	after_datatable_render(datatable) {
		try {
			setup_forecast_total_bar(datatable);
		} catch (e) {
			console.error("[Purchase Liability] dynamic total bar failed:", e);
		}
	},
};

function setup_forecast_total_bar(datatable) {
	const FIELD = "forecast_liability_lead"; // the lead "Forecast Liability" column
	const wrapperEl = datatable && (datatable.wrapper || (datatable.$wrapper && datatable.$wrapper[0]));
	if (!wrapperEl) return;
	const $wrapper = $(wrapperEl);

	let $bar = $wrapper.find(".plcf-total-bar");
	if (!$bar.length) {
		$bar = $(
			'<div class="plcf-total-bar" style="position:sticky;bottom:0;z-index:6;' +
				"background:var(--fg-color,#fff);border-top:2px solid var(--border-color,#d1d8dd);" +
				'padding:6px 12px;font-weight:700;text-align:right;font-size:13px;"></div>'
		);
		$wrapper.append($bar);
	}

	const recompute = () => {
		let total = 0;
		const dm = datatable.datamanager;
		if (!dm || !dm.getRow) return;
		$wrapper.find(".dt-row").each(function () {
			if (this.offsetParent === null) return; // filtered out / hidden
			const m = (this.className || "").match(/dt-row-(\d+)/);
			if (!m) return;
			let cells;
			try {
				cells = dm.getRow(parseInt(m[1], 10));
			} catch (e) {
				return;
			}
			const cell = (cells || []).find((c) => c && c.column && c.column.fieldname === FIELD);
			if (cell) total += flt(cell.content);
		});
		$bar.html(__("Total Forecast Liability (filtered):") + " Rs/- " + format_number(total, null, 2));
	};

	const debounced = frappe.utils.debounce(recompute, 150);
	// Re-total whenever an inline column filter changes.
	$wrapper.off(".plcf").on("input.plcf keyup.plcf change.plcf", ".dt-filter", debounced);
	recompute();
}
