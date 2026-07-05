// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

const PRR_METHOD_PATH =
	"playground.playground.report.production_requirement_report.production_requirement_report";

function prr_recompute_required_to_produce(row_values, new_buffer_qty) {
	let total_pending = 0;
	let total_reserved = 0;

	Object.keys(row_values).forEach((key) => {
		if (key.indexOf("pending_") === 0) total_pending += flt(row_values[key]);
		if (key.indexOf("reserved_") === 0) total_reserved += flt(row_values[key]);
	});

	const total_avlbl_stock = flt(row_values.total_avlbl_stock);
	const unfulfilled_demand = total_pending - total_reserved;

	return Math.max(0, unfulfilled_demand - total_avlbl_stock + flt(new_buffer_qty));
}

const PRR_CHARTJS_CDN = "https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js";

// Draw the top-20 "Pending Value (bars) + Pending Qty (line)" dual-axis chart.
// The server embeds the series (base64 JSON) on a canvas in the report message;
// frappe-charts can't do a second y-axis, so we render it with Chart.js here.
function prr_render_combo_chart() {
	const canvas = document.getElementById("prr-combo-chart");
	if (!canvas || !canvas.dataset.series) return;

	let series;
	try {
		series = JSON.parse(decodeURIComponent(escape(atob(canvas.dataset.series))));
	} catch (e) {
		return;
	}
	if (!series.labels || !series.labels.length) return;

	const draw = () => {
		if (!window.Chart) return;
		if (window._prrCombo) {
			try {
				window._prrCombo.destroy();
			} catch (e) {}
		}
		const ink =
			getComputedStyle(document.documentElement).getPropertyValue("--text-muted") || "#898781";
		const dark = window.matchMedia && matchMedia("(prefers-color-scheme: dark)").matches;
		const grid = dark ? "#2c2c2a" : "#e1e0d9";

		window._prrCombo = new Chart(canvas, {
			data: {
				labels: series.labels,
				datasets: [
					{
						type: "bar",
						label: __("Pending value"),
						data: series.value,
						yAxisID: "yValue",
						backgroundColor: "#2a78d6",
						borderRadius: 4,
						order: 2,
						categoryPercentage: 0.7,
						barPercentage: 0.9,
					},
					{
						type: "line",
						label: __("Pending qty"),
						data: series.qty,
						yAxisID: "yQty",
						borderColor: "#eb6834",
						backgroundColor: "#eb6834",
						borderWidth: 2,
						pointRadius: 3,
						pointHoverRadius: 5,
						tension: 0.3,
						fill: false,
						order: 1,
					},
					{
						type: "line",
						label: __("Qty to produce"),
						data: series.produce,
						yAxisID: "yQty",
						borderColor: "#199e70",
						backgroundColor: "#199e70",
						borderWidth: 2,
						borderDash: [5, 4],
						pointRadius: 3,
						pointStyle: "rectRot",
						pointHoverRadius: 5,
						tension: 0.3,
						fill: false,
						order: 0,
					},
				],
			},
			options: {
				responsive: true,
				maintainAspectRatio: false,
				interaction: { mode: "index", intersect: false },
				plugins: {
					legend: { display: false },
					tooltip: {
						callbacks: {
							label: (c) => "  " + c.dataset.label + ": " + Math.round(c.parsed.y).toLocaleString(),
						},
					},
				},
				scales: {
					x: {
						grid: { display: false },
						ticks: { color: ink, font: { size: 11 }, maxRotation: 60, minRotation: 55, autoSkip: false },
					},
					yValue: {
						position: "left",
						beginAtZero: true,
						grid: { color: grid },
						title: { display: true, text: __("Pending value"), color: ink, font: { size: 12 } },
						ticks: { color: ink, font: { size: 11 }, callback: (v) => Math.round(v / 1000) + "k" },
					},
					yQty: {
						position: "right",
						beginAtZero: true,
						grid: { drawOnChartArea: false },
						title: { display: true, text: __("Pending qty"), color: ink, font: { size: 12 } },
						ticks: { color: ink, font: { size: 11 }, callback: (v) => Math.round(v).toLocaleString() },
					},
				},
			},
		});
	};

	if (window.Chart) {
		draw();
	} else {
		frappe.require(PRR_CHARTJS_CDN, draw);
	}
}

frappe.query_reports["Production Requirement Report"] = {
	filters: [
		{
			fieldname: "item_code",
			label: __("FG Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "unreserved_basis",
			label: __("Unreserved Stock Basis"),
			fieldtype: "Select",
			options: ["All Reservations", "Only Displayed SOs"].join("\n"),
			default: "Only Displayed SOs",
			// What "Total Avlbl Free Stock" nets out of the stores warehouse
			// on-hand: every reservation (truly free stock), or only reservations
			// tied to the Sales Orders shown here.
		},
		{
			fieldname: "date_basis",
			label: __("Date Basis"),
			fieldtype: "Select",
			options: ["Document Creation Date", "Delivery Date", "Custom Updated Delivery Date"].join("\n"),
			default: "Custom Updated Delivery Date",
			// Which Sales Order date the From/To range filters on.
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "include_draft",
			label: __("Include Draft SOs"),
			fieldtype: "Check",
			default: 0,
			// Off: submitted SOs only. On: also include Draft (docstatus 0) SOs.
		},
		{
			fieldname: "hide_fulfilled",
			label: __("Hide Fulfilled SOs (no shortfall)"),
			fieldtype: "Check",
			default: 0,
			// Hides the column pair for any SO whose lines are all fully reserved.
			// Purely visual — Required to Produce still counts every open SO.
		},
	],

	// Cell formatting for readability:
	//  - highlight Required to Produce when there's a real shortfall,
	//  - flag negative free stock in red,
	//  - tint the per-SO columns: light grey for Pending, light pink for Reserved,
	//    so the alternating pairs are easy to tell apart.
	formatter(value, row, column, data, default_formatter) {
		let formatted = default_formatter(value, row, column, data);
		const fieldname = column.fieldname || "";

		if (data && fieldname === "required_to_produce" && flt(data.required_to_produce) > 0) {
			formatted = `<span style="color:#b02a37;font-weight:600;">${formatted}</span>`;
		} else if (data && fieldname === "total_avlbl_stock" && flt(data.total_avlbl_stock) < 0) {
			formatted = `<span style="color:#b02a37;">${formatted}</span>`;
		}

		if (fieldname.indexOf("pending_") === 0) {
			return `<div style="background-color:#f1f3f5;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		if (fieldname.indexOf("reserved_") === 0) {
			return `<div style="background-color:#fde2e7;margin:-8px -12px;padding:8px 12px;">${formatted}</div>`;
		}
		return formatted;
	},

	// Redraw the top-20 dual-axis chart after every run (the report message,
	// which carries the chart's data, is re-rendered on each run).
	after_datatable_render() {
		prr_render_combo_chart();
	},

	// Native Script Report columns hard-code editable:false when the DataTable is built
	// (see frappe/frappe#27414), so we flip it back on here. A Buffer Qty edit recomputes
	// Required to Produce inline AND is persisted back to Item.safety_stock via
	// update_buffer_qty (guarded by Item write permission on the server).
	get_datatable_options(datatable_options) {
		datatable_options.columns.forEach((column) => {
			if (column.id === "buffer_qty") {
				column.editable = true;
			}
		});

		datatable_options.events = datatable_options.events || {};
		const original_on_submit_editing = datatable_options.events.onSubmitEditing;

		datatable_options.events.onSubmitEditing = function (cell) {
			if (original_on_submit_editing) {
				original_on_submit_editing(cell);
			}

			// cell = [row_values (before edit), cell_id, new_val]
			const [row_values, cell_id, new_val] = cell;
			if (cell_id !== "buffer_qty") return;

			let buffer_qty = flt(new_val);
			if (buffer_qty < 0) {
				frappe.show_alert({ message: __("Buffer Qty cannot be negative."), indicator: "red" });
				buffer_qty = 0;
			}

			const required_to_produce = prr_recompute_required_to_produce(row_values, buffer_qty);

			// Keep the report's in-memory data array in sync (used by Create
			// Production Plan, exports, and the redraw below).
			const report_row = (frappe.query_report.data || []).find(
				(r) => r.item_code === row_values.item_code
			);
			if (report_row) {
				report_row.buffer_qty = buffer_qty;
				report_row.required_to_produce = required_to_produce;
			}

			if (frappe.query_report.datatable) {
				frappe.query_report.datatable.refresh(frappe.query_report.data);
			}

			// Persist to the Item master (Item.safety_stock). On failure the
			// server surfaces the error dialog; the in-memory value stays until
			// the next reload, which will re-read the (unchanged) master value.
			frappe.call({
				method: `${PRR_METHOD_PATH}.update_buffer_qty`,
				args: { item_code: row_values.item_code, buffer_qty: buffer_qty },
				callback() {
					frappe.show_alert({
						message: __("Buffer Qty saved to Item master ({0}).", [row_values.item_code]),
						indicator: "green",
					});
				},
			});
		};

		return datatable_options;
	},

	onload(report) {
		report.page.add_inner_button(__("Create Production Plan"), () => {
			const data = frappe.query_report.data || [];
			const items = data
				.filter((row) => flt(row.required_to_produce) > 0)
				.map((row) => ({ item_code: row.item_code, qty: row.required_to_produce }));

			if (!items.length) {
				frappe.msgprint(__("No rows currently have a Required to Produce quantity greater than zero."));
				return;
			}

			const item_list_html = items
				.map((i) => `<li>${frappe.utils.escape_html(i.item_code)}: ${i.qty}</li>`)
				.join("");

			frappe.confirm(
				__("This will create a draft Production Plan, broken down by Sales Order, covering {0} item(s):<ul>{1}</ul>Each item's rows sum to its Required to Produce (buffer shown as a separate unlinked row). You'll be able to review and edit before submitting. Continue?", [
					items.length,
					item_list_html,
				]),
				() => {
					frappe.call({
						method: `${PRR_METHOD_PATH}.create_production_plan`,
						args: { filters: JSON.stringify(frappe.query_report.get_filter_values()) },
						freeze: true,
						freeze_message: __("Creating Production Plan..."),
						callback: function (r) {
							if (r.message) {
								frappe.set_route("Form", "Production Plan", r.message);
							}
						},
					});
				}
			);
		});
	},
};
