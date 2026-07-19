import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, fmt_money, formatdate, now_datetime


class WeeklyPlanningSnapshot(Document):
	def validate(self):
		# Not doctype-level unique on snapshot_date: cancel+amend needs to be
		# able to insert a new doc at the same date once the old one is
		# cancelled. Only a SUBMITTED snapshot on the same date is blocked.
		duplicate = frappe.db.exists(
			"Weekly Planning Snapshot",
			{
				"snapshot_date": self.snapshot_date,
				"docstatus": 1,
				"name": ["!=", self.name],
			},
		)
		if duplicate:
			frappe.throw(
				_("A submitted Weekly Planning Snapshot ({0}) already exists for {1}.").format(
					duplicate, formatdate(self.snapshot_date)
				)
			)

		self._recompute_lines()
		self._render_consolidated()

	def on_submit(self):
		self.db_set("approved_by", frappe.session.user)
		self.db_set("approved_on", now_datetime())

	# ------------------------------------------------------------------ #
	def _recompute_lines(self):
		"""Suggested Prodn = max(0, (pending - reserved) - item_free_stock) (FGSRM
		logic). Committed Prodn is prepopulated from Suggested only when it's empty
		- user edits are never clobbered."""
		for d in self.items:
			if d.is_buffer:
				# Synthetic surplus row - no SO requirement; keep its Committed as set.
				d.suggested_prodn = 0.0
				continue
			d.suggested_prodn = max(0.0, max(0.0, flt(d.pending_qty) - flt(d.reserved_qty)) - flt(d.item_free_stock))
			# Default Committed to Suggested only when unset - preserve edits
			# (including a deliberate 0).
			if d.committed_prodn is None or d.committed_prodn == "":
				d.committed_prodn = d.suggested_prodn

	def _render_consolidated(self):
		"""Per-item summary (Item, Item Free Stock, total Suggested, total
		Committed) into the read-only HTML field."""
		by_item = {}
		order = []
		for d in self.items:
			ic = d.item_code
			if ic not in by_item:
				by_item[ic] = {
					"item_name": d.item_name,
					"item_free_stock": flt(d.item_free_stock),
					"suggested": 0.0,
					"committed": 0.0,
				}
				order.append(ic)
			by_item[ic]["suggested"] += flt(d.suggested_prodn)
			by_item[ic]["committed"] += flt(d.committed_prodn)

		rows = "".join(
			"<tr><td>{item}</td><td>{name}</td><td style='text-align:right'>{free}</td>"
			"<td style='text-align:right'>{sug}</td><td style='text-align:right'><b>{com}</b></td></tr>".format(
				item=frappe.utils.escape_html(ic),
				name=frappe.utils.escape_html(by_item[ic]["item_name"] or ""),
				free=fmt_money(by_item[ic]["item_free_stock"]),
				sug=fmt_money(by_item[ic]["suggested"]),
				com=fmt_money(by_item[ic]["committed"]),
			)
			for ic in order
		)
		total_com = sum(v["committed"] for v in by_item.values())
		self.consolidated_requirement_html = (
			"<table class='table table-bordered' style='font-size:12px;'>"
			"<thead><tr>"
			"<th>{h_item}</th><th>{h_name}</th><th style='text-align:right'>{h_free}</th>"
			"<th style='text-align:right'>{h_sug}</th><th style='text-align:right'>{h_com}</th>"
			"</tr></thead><tbody>{rows}</tbody>"
			"<tfoot><tr><th colspan='4' style='text-align:right'>{h_total}</th>"
			"<th style='text-align:right'>{total}</th></tr></tfoot>"
			"</table>"
		).format(
			h_item=_("Item"), h_name=_("Item Name"), h_free=_("Item Free Stock"),
			h_sug=_("Total Suggested"), h_com=_("Total Committed"),
			h_total=_("Total Committed Prodn"), total=fmt_money(total_com), rows=rows,
		)
