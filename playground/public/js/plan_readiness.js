// Copyright (c) 2026, Frontec and contributors
// For license information, please see license.txt

// Production Plan readiness flow, shared by the FG Stock Reservation Manager
// report and the Weekly Planning Snapshot form (loaded with frappe.require).
//
//   1. Check   - check_production_plan_readiness walks every FG's BOM tree and
//                returns blockers / warnings, each with fix links.
//   2. Dialog  - shown only when something was found: the issues with
//                "Create BOM" / "Submit draft" / "Open Item" buttons (new tab),
//                a Re-check button, and three ways on:
//                  Create Prodn Plan (cutting off broken branches, or leaving
//                  out affected finished goods), or Download workbook only.
//   3. Build   - creates the plan, then always downloads the workbook; anything
//                left out is summarised here and listed on its Action Items sheet.
//
// See playground/playground/plan_readiness.py for the checks themselves.

frappe.provide("playground.plan_readiness");

const PR_METHOD_PATH =
	"playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager";

// opts = {
//   check_args:   args for check_production_plan_readiness ({filters} or {snapshot})
//   build_method: create_production_plan_from_suggested_prodn / _from_snapshot
//   build_args:   args for build_method
//   confirm_text: confirmation shown when the check is clean
//   download(plan_name_or_null): downloads the unified workbook
// }
playground.plan_readiness.run = function (opts) {
	pr_check(opts, (res) => {
		if (!res.blocker_count && !res.warning_count) {
			frappe.confirm(opts.confirm_text, () => pr_build(opts, "branches"));
			return;
		}
		pr_dialog(opts, res);
	});
};

function pr_check(opts, callback) {
	frappe.call({
		method: `${PR_METHOD_PATH}.check_production_plan_readiness`,
		args: opts.check_args,
		freeze: true,
		freeze_message: __("Checking BOMs and item data…"),
		callback(r) {
			if (r.message) callback(r.message);
		},
	});
}

function pr_dialog(opts, res) {
	const d = new frappe.ui.Dialog({
		title: __("Production Plan readiness"),
		size: "extra-large",
		fields: [
			{ fieldtype: "HTML", fieldname: "issues_html" },
			{
				fieldtype: "Select",
				fieldname: "exclude_mode",
				label: __("If blockers remain"),
				options: [
					{ value: "branches", label: __("Cut off only the broken branches — plan the rest of each finished good") },
					{ value: "fgs", label: __("Leave out every finished good with a blocker anywhere in its BOM") },
				],
				default: "branches",
			},
		],
		primary_action_label: __("Create Prodn Plan"),
		primary_action(values) {
			d.hide();
			pr_build(opts, values.exclude_mode || "branches");
		},
		secondary_action_label: __("Re-check"),
		secondary_action() {
			pr_check(opts, (fresh) => pr_render(d, fresh));
		},
	});
	d.add_custom_action(
		__("Download workbook only"),
		() => {
			d.hide();
			opts.download(null);
		},
		"btn-default"
	);
	pr_render(d, res);
	d.show();
}

function pr_render(d, res) {
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const blockers = res.issues.filter((i) => i.severity === "blocker");
	const warnings = res.issues.filter((i) => i.severity !== "blocker");
	const affected = Object.keys(res.tree_blocked || {}).length;
	const unplannable = Object.keys(res.root_blocked || {}).length;

	const action_buttons = (actions) =>
		(actions || [])
			.map(
				(a) =>
					`<a class="btn btn-xs btn-default" style="margin:0 4px 4px 0" href="${encodeURI(a.route)}" target="_blank" rel="noopener">${esc(a.label)}</a>`
			)
			.join("");

	const table = (issues, show_fgs) => `
		<table class="table table-bordered table-sm" style="margin-bottom:8px">
			<thead><tr>
				<th style="width:22%">${__("Item")}</th>
				<th>${__("Problem")}</th>
				<th style="width:22%">${__("Where in BOM")}</th>
				${show_fgs ? `<th style="width:9%">${__("Affects")}</th>` : ""}
				<th style="width:24%">${__("Fix")}</th>
			</tr></thead>
			<tbody>${issues
				.map(
					(i) => `<tr>
					<td><b>${esc(i.item_code)}</b><br><span class="text-muted small">${esc(i.item_name)}</span></td>
					<td>${esc(i.message)}</td>
					<td class="small">${esc((i.path || []).join(" › "))}</td>
					${
						show_fgs
							? `<td class="small" title="${esc((i.fgs || []).join(", "))}">${__("{0} FG", [(i.fgs || []).length])}</td>`
							: ""
					}
					<td>${action_buttons(i.actions)}</td>
				</tr>`
				)
				.join("")}</tbody>
		</table>`;

	let html = `<p>${__("{0} finished good(s) to plan.", [res.items])} `;
	if (blockers.length) {
		html += `<span class="indicator-pill red">${__("{0} blocker(s)", [blockers.length])}</span> `;
		html += __("affecting {0} finished good(s); {1} can't be planned at all.", [affected, unplannable]);
	} else {
		html += `<span class="indicator-pill green">${__("No blockers")}</span>`;
	}
	html += `</p><p class="text-muted small">${__(
		"Fix buttons open in a new tab — fix, come back and click Re-check. Anything still broken is left out of the plan and listed on the workbook's Action Items sheet."
	)}</p>`;
	html += `<div style="max-height:55vh;overflow:auto">`;
	if (blockers.length) {
		html += `<h5 style="margin-top:8px">${__("Blockers")}</h5>${table(blockers, true)}`;
	}
	if (warnings.length) {
		html += `<details ${blockers.length ? "" : "open"}><summary style="cursor:pointer;margin:8px 0"><b>${__(
			"Warnings ({0}) — the plan builds, but the workbook is less useful",
			[warnings.length]
		)}</b></summary>${table(warnings, false)}</details>`;
	}
	html += `</div>`;

	d.fields_dict.issues_html.$wrapper.html(html);
	d.set_df_property("exclude_mode", "hidden", blockers.length ? 0 : 1);
}

function pr_build(opts, exclude_mode) {
	frappe.call({
		method: opts.build_method,
		args: Object.assign({}, opts.build_args, { exclude_mode }),
		freeze: true,
		freeze_message: __("Creating Production Plan…"),
		callback(r) {
			const m = r.message;
			if (!m || !m.name) return;
			pr_report(m);
			// Always hand over the workbook - a partial plan is still useful, and its
			// Action Items sheet carries every fix link.
			opts.download(m.name);
		},
	});
}

function pr_report(m) {
	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
	const excluded = m.excluded || [];
	const not_planned = m.not_planned || [];
	const plan_link = `<a href="/app/production-plan/${encodeURIComponent(m.name)}" target="_blank">${esc(m.name)}</a>`;

	if (m.handed_off && !excluded.length && !not_planned.length) {
		frappe.show_alert({
			message: __("Production Plan {0}: {1} item(s), {2} raw material line(s), full chain built. Downloading unified planning workbook…", [
				m.name,
				m.items,
				m.raw_materials,
			]),
			indicator: "green",
		});
		return;
	}

	let html = `<p>${__("Draft Production Plan {0} created with {1} item(s). The unified planning workbook is downloading — its Action Items sheet lists everything below with fix links.", [plan_link, m.items])}</p>`;
	if (m.handoff_error) {
		html += `<p class="text-danger"><b>${__("The nested chain could not be built:")}</b> ${esc(m.handoff_error)}<br>${__("Fix the cause, then open the plan and click “Create Full Chain”.")}</p>`;
	} else if (!m.handed_off) {
		html += `<p>${__("Open the plan and click “Create Full Chain” to build the nested chain.")}</p>`;
	}
	if (excluded.length) {
		html += `<p><b>${__("Finished goods left out ({0})", [excluded.length])}</b></p><ul>${excluded
			.map((e) => `<li><b>${esc(e.item_code)}</b> (${esc(e.qty)}) — ${esc(e.reason)}</li>`)
			.join("")}</ul>`;
	}
	if (not_planned.length) {
		html += `<p><b>${__("Sub-assemblies not planned ({0})", [not_planned.length])}</b> — ${__("materials below them are missing from the plan")}</p><ul>${not_planned
			.map(
				(s) =>
					`<li><b>${esc(s.item_code)}</b> — ${esc(s.reason)} <a href="/app/bom/new?item=${encodeURIComponent(s.item_code)}" target="_blank">${__("Create BOM")}</a></li>`
			)
			.join("")}</ul>`;
	}
	frappe.msgprint({
		title: __("Production Plan created — needs attention"),
		indicator: m.handoff_error ? "red" : "orange",
		message: html,
		wide: true,
	});
}
