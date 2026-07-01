frappe.ui.form.on("Kit Content Framework Item", {
	node_name(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.node_name) return;
		// Pre-fill keep_aggregated from the master default. This is only
		// a starting value — the user can override it freely on this row
		// without affecting the master or any other framework/mapping that
		// uses the same node.
		frappe.db
			.get_value("Kit Content Node", row.node_name, "keep_aggregated")
			.then((r) => {
				if (r && r.message != null) {
					const val =
						typeof r.message === "object"
							? r.message.keep_aggregated || 0
							: r.message || 0;
					frappe.model.set_value(cdt, cdn, "keep_aggregated", val);
				}
			});
	},
});
