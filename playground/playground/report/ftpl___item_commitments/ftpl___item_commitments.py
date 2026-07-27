# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
FTPL - Item Commitments
=======================

A unified inventory commitment and availability explorer for a single Item.
It answers one question - "Why is this item (un)available?" - by pulling every
document that consumes, reserves, produces or otherwise affects the item's
availability into one place, so a planner no longer has to open Projected Qty,
Stock Balance, Stock Reservation Entries, Sales Orders, Work Orders, Material
Requests, Pick Lists, Production Plans and Stock Entries one at a time.

Output (a single Script Report `execute` return: columns, data, message, chart,
report_summary):

  * report_summary  -> Section 1 "Inventory Position" number cards.
  * message (HTML)  -> the full Inventory Position table, the "Why Not
                       Available?" reconciliation, and any Intelligent Warnings.
  * columns + data  -> Section 2 "Commitment Explorer": one row per document,
                       coloured by Direction, each Document a Dynamic Link that
                       opens the original ERP document, with a Running Available
                       balance walked down the sorted rows.

Netting model (IMPORTANT - documented so the numbers are unambiguous)
---------------------------------------------------------------------
Availability is reconciled exactly as the brief's diagnostic example:

    Net Available = Actual
                    - Stock Reservation Entries        (firm, against SO & WO)
                    - Work Order raw-material requirement still needed
                    + Work Order production outstanding
                    + Purchase Order outstanding

Only those four categories move Running Available / Net Available. To avoid
double counting:

  * Sales Order demand is shown for context (Direction = Outgoing) but does NOT
    net - the firm consumption of an SO is its Stock Reservation Entry. Any SO
    demand that is not reserved is surfaced as an Intelligent Warning instead.
  * Work Order raw-material rows net only the UNRESERVED remaining requirement
    (required - consumed - already reserved for that WO); the reserved slice is
    already netted through its Stock Reservation Entry row.
  * Purchase Receipts, Material Requests, Production Plans, Pick Lists and Stock
    Entries are informational (they are already reflected in Actual, or are
    speculative planning), so they are listed with a blank Running Available.

One aggregated SQL statement is issued per document source (no N+1); every
result set is normalised to a common commitment-row dict, the rows are merged,
sorted and the running balance is computed in Python.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate

from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses


# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

INCOMING = "Incoming"
OUTGOING = "Outgoing"
RESERVATION = "Reservation"
PLANNING = "Planning"

# Sort priority per commitment type (lower sorts first within the same date):
# firm consumption first, then demand, then incoming supply, then planning.
PRIORITY = {
	"Stock Reservation": 1,
	"Sales Order": 2,
	"Pick List": 3,
	"Work Order Consumption": 4,
	"Work Order Production": 5,
	"Purchase Order": 6,
	"Purchase Receipt": 7,
	"Material Request": 8,
	"Production Plan": 9,
	"Stock Entry": 10,
}

# Statuses that mean a document is no longer influencing inventory. Hidden
# unless "Include Closed Documents" is ticked.
CLOSED_STATUSES = {"Completed", "Closed", "Stopped", "Cancelled", "Delivered"}


def execute(filters=None):
	return ItemCommitments(filters).run()


class ItemCommitments:
	def __init__(self, filters=None):
		self.filters = frappe._dict(filters or {})
		self.item_code = self.filters.get("item_code")
		self.company = self.filters.get("company")
		self.rows = []

		# Running reconciliation buckets (all held as positive magnitudes).
		self.actual_qty = 0.0
		self.reserved_total = 0.0  # Stock Reservation Entries (SO + WO)
		self.reserved_so = 0.0
		self.reserved_wo = 0.0
		self.wo_required = 0.0  # unreserved WO raw-material requirement
		self.wo_incoming = 0.0  # WO production outstanding
		self.po_incoming = 0.0  # Purchase Order outstanding
		self.so_demand = 0.0  # Sales Order pending (context only)
		self.projected_qty = 0.0  # ERPNext Bin projected qty (for reference)
		self.item_name = ""
		self.stock_uom = ""

	# ------------------------------------------------------------------ #
	# Orchestration
	# ------------------------------------------------------------------ #

	def run(self):
		if not (self.company and self.item_code):
			frappe.msgprint(
				_("Please select both a Company and an Item."), indicator="orange", alert=True
			)
			return self.get_columns(), []

		self.warehouses = self.get_warehouses()
		self.load_item()
		self.load_bin_summary()

		# Pre-compute the map of qty reserved per Work Order so the Work Order
		# consumption rows can net only the unreserved remainder (no double count
		# against the Stock Reservation Entry rows).
		self.wo_reserved_map = self.get_wo_reserved_map()

		# One aggregated query per source; each appends normalised rows.
		self.collect_stock_reservations()
		self.collect_sales_orders()
		self.collect_work_orders()
		self.collect_material_requests()
		self.collect_purchase_orders()
		self.collect_purchase_receipts()
		if self.filters.get("include_production_plans"):
			self.collect_production_plans()
		self.collect_pick_lists()
		self.collect_stock_entries()

		self.sort_and_walk()

		message = self.build_message()
		report_summary = self.build_report_summary()

		data = [] if self.filters.get("show_summary_only") else self.rows
		return self.get_columns(), data, message, None, report_summary

	# ------------------------------------------------------------------ #
	# Setup helpers
	# ------------------------------------------------------------------ #

	def get_warehouses(self):
		"""Selected warehouse + its children, or None to mean 'all warehouses'."""
		wh = self.filters.get("warehouse")
		if not wh:
			return None
		children = get_child_warehouses(wh) or [wh]
		return list(set(children))

	def _wh_clause(self, alias):
		"""SQL fragment + params to restrict `alias` to the selected warehouses.
		Returns ('', {}) when no warehouse filter is set."""
		if not self.warehouses:
			return "", {}
		return f" AND {alias} IN %(warehouses)s ", {"warehouses": tuple(self.warehouses)}

	def _docstatus_values(self):
		"""(1,) normally; (0, 1) when 'Include Draft Documents' is ticked."""
		if self.filters.get("include_draft"):
			return (0, 1)
		return (1,)

	def load_item(self):
		item = frappe.db.get_value(
			"Item", self.item_code, ["item_name", "stock_uom"], as_dict=True
		)
		if item:
			self.item_name = item.item_name or ""
			self.stock_uom = item.stock_uom or ""

	def load_bin_summary(self):
		"""Actual + ERPNext projected qty straight from Bin (on-hand is a ledger
		fact, not document-derived, so it is read here rather than reconstructed)."""
		wh_clause, params = self._wh_clause("warehouse")
		params["item_code"] = self.item_code
		row = frappe.db.sql(
			f"""
			SELECT
				COALESCE(SUM(actual_qty), 0)     AS actual_qty,
				COALESCE(SUM(projected_qty), 0)  AS projected_qty
			FROM `tabBin`
			WHERE item_code = %(item_code)s
			{wh_clause}
			""",
			params,
			as_dict=True,
		)
		if row:
			self.actual_qty = flt(row[0].actual_qty)
			self.projected_qty = flt(row[0].projected_qty)

	def get_wo_reserved_map(self):
		"""qty of THIS item reserved for production per Work Order, from active
		Stock Reservation Entries whose voucher is a Work Order."""
		wh_clause, params = self._wh_clause("warehouse")
		params["item_code"] = self.item_code
		rows = frappe.db.sql(
			f"""
			SELECT voucher_no, COALESCE(SUM(reserved_qty - delivered_qty), 0) AS qty
			FROM `tabStock Reservation Entry`
			WHERE docstatus = 1
				AND voucher_type = 'Work Order'
				AND item_code = %(item_code)s
				{wh_clause}
			GROUP BY voucher_no
			""",
			params,
			as_dict=True,
		)
		return {r.voucher_no: flt(r.qty) for r in rows}

	# ------------------------------------------------------------------ #
	# Row factory
	# ------------------------------------------------------------------ #

	def add_row(
		self,
		*,
		direction,
		commitment_type,
		document_type,
		document,
		status,
		date,
		warehouse,
		qty,
		reference,
		remarks,
		signed,
	):
		"""Append one normalised commitment row. `signed` is the effect on Running
		Available (already sign-adjusted); 0 means informational (balance blank)."""
		self.rows.append(
			frappe._dict(
				{
					"priority": PRIORITY.get(commitment_type, 99),
					"direction": direction,
					"commitment_type": commitment_type,
					"document_type": document_type,
					"document": document,
					"status": status,
					"date": getdate(date) if date else None,
					"warehouse": warehouse,
					"qty": flt(qty),
					"running_available": None,
					"reference": reference,
					"remarks": remarks,
					"_signed": flt(signed),
				}
			)
		)

	# ------------------------------------------------------------------ #
	# Source 1 - Stock Reservation Entry (firm reservations)
	# ------------------------------------------------------------------ #

	def collect_stock_reservations(self):
		wh_clause, params = self._wh_clause("sre.warehouse")
		params.update({"item_code": self.item_code, "company": self.company})
		rows = frappe.db.sql(
			f"""
			SELECT
				sre.name,
				sre.warehouse,
				sre.voucher_type,
				sre.voucher_no,
				sre.status,
				(sre.reserved_qty - sre.delivered_qty) AS qty,
				so.customer,
				wo.production_item
			FROM `tabStock Reservation Entry` sre
			LEFT JOIN `tabSales Order` so
				ON so.name = sre.voucher_no AND sre.voucher_type = 'Sales Order'
			LEFT JOIN `tabWork Order` wo
				ON wo.name = sre.voucher_no AND sre.voucher_type = 'Work Order'
			WHERE sre.docstatus = 1
				AND sre.item_code = %(item_code)s
				AND sre.company = %(company)s
				AND (sre.reserved_qty - sre.delivered_qty) > 0
				{wh_clause}
			""",
			params,
			as_dict=True,
		)
		for r in rows:
			qty = flt(r.qty)
			if r.voucher_type == "Sales Order":
				self.reserved_so += qty
				reference = _("Customer: {0}").format(r.customer) if r.customer else ""
				remarks = _("Reserved against Sales Order")
			elif r.voucher_type == "Work Order":
				self.reserved_wo += qty
				reference = _("Producing: {0}").format(r.production_item) if r.production_item else ""
				remarks = _("Reserved for Work Order production")
			else:
				reference = ""
				remarks = _("Reserved against {0}").format(r.voucher_type)
			self.reserved_total += qty
			self.add_row(
				direction=RESERVATION,
				commitment_type="Stock Reservation",
				document_type=r.voucher_type,
				document=r.voucher_no,
				status=r.status,
				date=None,
				warehouse=r.warehouse,
				qty=qty,
				reference=reference,
				remarks=remarks,
				signed=-qty,
			)

	# ------------------------------------------------------------------ #
	# Source 2 - Sales Orders (outstanding demand, context only)
	# ------------------------------------------------------------------ #

	def collect_sales_orders(self):
		wh_clause, params = self._wh_clause("soi.warehouse")
		params.update({"item_code": self.item_code, "company": self.company})
		status_clause = self._closed_clause("so.status")
		docstatus = self._docstatus_values()
		params["docstatus"] = docstatus
		rows = frappe.db.sql(
			f"""
			SELECT
				so.name,
				so.customer,
				so.status,
				so.delivery_date,
				so.transaction_date,
				soi.warehouse,
				soi.delivery_date AS item_delivery_date,
				(soi.qty - soi.delivered_qty) AS pending_qty
			FROM `tabSales Order Item` soi
			INNER JOIN `tabSales Order` so ON so.name = soi.parent
			WHERE soi.item_code = %(item_code)s
				AND so.company = %(company)s
				AND so.docstatus IN %(docstatus)s
				AND (soi.qty - soi.delivered_qty) > 0
				{status_clause}
				{wh_clause}
			""",
			params,
			as_dict=True,
		)
		for r in rows:
			pending = flt(r.pending_qty)
			self.so_demand += pending
			self.add_row(
				direction=OUTGOING,
				commitment_type="Sales Order",
				document_type="Sales Order",
				document=r.name,
				status=r.status,
				date=r.item_delivery_date or r.delivery_date or r.transaction_date,
				warehouse=r.warehouse,
				qty=pending,
				reference=_("Customer: {0}").format(r.customer) if r.customer else "",
				# Context only - the firm effect of an SO is its Stock Reservation
				# Entry row; any unreserved demand surfaces as a warning instead.
				remarks=_("Pending delivery to customer"),
				signed=0,
			)

	# ------------------------------------------------------------------ #
	# Source 3 - Work Orders (consumption + production)
	# ------------------------------------------------------------------ #

	def collect_work_orders(self):
		docstatus = self._docstatus_values()
		status_clause = self._closed_clause("wo.status")

		# 3a. Production - incoming finished goods (item is the produced item).
		params = {"item_code": self.item_code, "company": self.company, "docstatus": docstatus}
		wh_clause, wh_params = self._wh_clause("wo.fg_warehouse")
		params.update(wh_params)
		prod_rows = frappe.db.sql(
			f"""
			SELECT
				wo.name,
				wo.status,
				wo.production_item,
				wo.fg_warehouse,
				wo.planned_start_date,
				(wo.qty - wo.produced_qty) AS pending_qty
			FROM `tabWork Order` wo
			WHERE wo.production_item = %(item_code)s
				AND wo.company = %(company)s
				AND wo.docstatus IN %(docstatus)s
				AND (wo.qty - wo.produced_qty) > 0
				{status_clause}
				{wh_clause}
			""",
			params,
			as_dict=True,
		)
		for r in prod_rows:
			pending = flt(r.pending_qty)
			self.wo_incoming += pending
			self.add_row(
				direction=INCOMING,
				commitment_type="Work Order Production",
				document_type="Work Order",
				document=r.name,
				status=r.status,
				date=r.planned_start_date,
				warehouse=r.fg_warehouse,
				qty=pending,
				reference=_("Producing: {0}").format(r.production_item),
				remarks=_("Finished goods expected from Work Order"),
				signed=+pending,
			)

		# 3b. Consumption - raw material required by open Work Orders (item is a
		# component). Net only the UNRESERVED remaining requirement.
		params = {"item_code": self.item_code, "company": self.company, "docstatus": docstatus}
		wh_clause, wh_params = self._wh_clause("woi.source_warehouse")
		params.update(wh_params)
		cons_rows = frappe.db.sql(
			f"""
			SELECT
				wo.name,
				wo.status,
				wo.production_item,
				woi.source_warehouse,
				wo.planned_start_date,
				(woi.required_qty - woi.consumed_qty) AS pending_qty
			FROM `tabWork Order Item` woi
			INNER JOIN `tabWork Order` wo ON wo.name = woi.parent
			WHERE woi.item_code = %(item_code)s
				AND wo.company = %(company)s
				AND wo.docstatus IN %(docstatus)s
				AND (woi.required_qty - woi.consumed_qty) > 0
				{status_clause}
				{wh_clause}
			""",
			params,
			as_dict=True,
		)
		for r in cons_rows:
			pending = flt(r.pending_qty)
			# Subtract the portion already reserved for this WO (netted via its
			# Stock Reservation Entry row) so it is not double counted.
			reserved_for_wo = self.wo_reserved_map.get(r.name, 0.0)
			net_required = max(0.0, pending - reserved_for_wo)
			self.wo_required += net_required
			if reserved_for_wo > 0.0001:
				remarks = _("Raw material required by Work Order ({0} reserved)").format(
					fmt_qty(reserved_for_wo)
				)
			else:
				remarks = _("Raw material required by Work Order")
			self.add_row(
				direction=OUTGOING,
				commitment_type="Work Order Consumption",
				document_type="Work Order",
				document=r.name,
				status=r.status,
				date=r.planned_start_date,
				warehouse=r.source_warehouse,
				qty=pending,
				reference=_("For: {0}").format(r.production_item),
				remarks=remarks,
				signed=-net_required,
			)

	# ------------------------------------------------------------------ #
	# Source 4 - Material Requests (planning demand / incoming)
	# ------------------------------------------------------------------ #

	def collect_material_requests(self):
		docstatus = self._docstatus_values()
		status_clause = self._closed_clause("mr.status")
		wh_clause, params = self._wh_clause("mri.warehouse")
		params.update({"item_code": self.item_code, "company": self.company, "docstatus": docstatus})
		rows = frappe.db.sql(
			f"""
			SELECT
				mr.name,
				mr.status,
				mr.material_request_type,
				mr.transaction_date,
				mri.warehouse,
				mri.schedule_date,
				(mri.qty - mri.ordered_qty) AS pending_qty
			FROM `tabMaterial Request Item` mri
			INNER JOIN `tabMaterial Request` mr ON mr.name = mri.parent
			WHERE mri.item_code = %(item_code)s
				AND mr.company = %(company)s
				AND mr.docstatus IN %(docstatus)s
				AND (mri.qty - mri.ordered_qty) > 0
				{status_clause}
				{wh_clause}
			""",
			params,
			as_dict=True,
		)
		remark_by_type = {
			"Purchase": _("Pending procurement (Material Request)"),
			"Material Transfer": _("Pending transfer (Material Request)"),
			"Material Issue": _("Pending issue (Material Request)"),
			"Manufacture": _("Pending manufacture (Material Request)"),
		}
		for r in rows:
			self.add_row(
				direction=PLANNING,
				commitment_type="Material Request",
				document_type="Material Request",
				document=r.name,
				status=r.status,
				date=r.schedule_date or r.transaction_date,
				warehouse=r.warehouse,
				qty=flt(r.pending_qty),
				reference=r.material_request_type or "",
				remarks=remark_by_type.get(r.material_request_type, _("Pending Material Request")),
				signed=0,  # planning - speculative, does not move firm availability
			)

	# ------------------------------------------------------------------ #
	# Source 5 - Purchase Orders (incoming supply)
	# ------------------------------------------------------------------ #

	def collect_purchase_orders(self):
		docstatus = self._docstatus_values()
		status_clause = self._closed_clause("po.status")
		wh_clause, params = self._wh_clause("poi.warehouse")
		params.update({"item_code": self.item_code, "company": self.company, "docstatus": docstatus})
		rows = frappe.db.sql(
			f"""
			SELECT
				po.name,
				po.status,
				po.supplier,
				po.transaction_date,
				poi.warehouse,
				poi.schedule_date,
				(poi.qty - poi.received_qty) AS pending_qty
			FROM `tabPurchase Order Item` poi
			INNER JOIN `tabPurchase Order` po ON po.name = poi.parent
			WHERE poi.item_code = %(item_code)s
				AND po.company = %(company)s
				AND po.docstatus IN %(docstatus)s
				AND (poi.qty - poi.received_qty) > 0
				{status_clause}
				{wh_clause}
			""",
			params,
			as_dict=True,
		)
		for r in rows:
			pending = flt(r.pending_qty)
			self.po_incoming += pending
			self.add_row(
				direction=INCOMING,
				commitment_type="Purchase Order",
				document_type="Purchase Order",
				document=r.name,
				status=r.status,
				date=r.schedule_date or r.transaction_date,
				warehouse=r.warehouse,
				qty=pending,
				reference=_("Supplier: {0}").format(r.supplier) if r.supplier else "",
				remarks=_("Pending purchase (incoming supply)"),
				signed=+pending,
			)

	# ------------------------------------------------------------------ #
	# Source 6 - Purchase Receipts (incoming already received, informational)
	# ------------------------------------------------------------------ #

	def collect_purchase_receipts(self):
		# Submitted receipts are already in Actual Qty, so they are listed for
		# traceability only (blank Running Available). Draft receipts appear only
		# when "Include Draft Documents" is ticked.
		docstatus = self._docstatus_values()
		status_clause = self._closed_clause("pr.status")
		wh_clause, params = self._wh_clause("pri.warehouse")
		params.update({"item_code": self.item_code, "company": self.company, "docstatus": docstatus})
		rows = frappe.db.sql(
			f"""
			SELECT
				pr.name,
				pr.status,
				pr.supplier,
				pr.posting_date,
				pri.warehouse,
				pri.qty
			FROM `tabPurchase Receipt Item` pri
			INNER JOIN `tabPurchase Receipt` pr ON pr.name = pri.parent
			WHERE pri.item_code = %(item_code)s
				AND pr.company = %(company)s
				AND pr.docstatus IN %(docstatus)s
				AND pri.qty > 0
				{status_clause}
				{wh_clause}
			ORDER BY pr.posting_date DESC
			LIMIT 50
			""",
			params,
			as_dict=True,
		)
		for r in rows:
			self.add_row(
				direction=INCOMING,
				commitment_type="Purchase Receipt",
				document_type="Purchase Receipt",
				document=r.name,
				status=r.status,
				date=r.posting_date,
				warehouse=r.warehouse,
				qty=flt(r.qty),
				reference=_("Supplier: {0}").format(r.supplier) if r.supplier else "",
				remarks=_("Received (already in Actual Qty)"),
				signed=0,
			)

	# ------------------------------------------------------------------ #
	# Source 7 - Production Plans (optional future demand)
	# ------------------------------------------------------------------ #

	def collect_production_plans(self):
		docstatus = self._docstatus_values()
		status_clause = self._closed_clause("pp.status")
		wh_clause, params = self._wh_clause("ppi.warehouse")
		params.update({"item_code": self.item_code, "company": self.company, "docstatus": docstatus})
		rows = frappe.db.sql(
			f"""
			SELECT
				pp.name,
				pp.status,
				pp.posting_date,
				ppi.warehouse,
				ppi.planned_start_date,
				COALESCE(ppi.pending_qty, ppi.planned_qty) AS qty
			FROM `tabProduction Plan Item` ppi
			INNER JOIN `tabProduction Plan` pp ON pp.name = ppi.parent
			WHERE ppi.item_code = %(item_code)s
				AND pp.company = %(company)s
				AND pp.docstatus IN %(docstatus)s
				AND COALESCE(ppi.pending_qty, ppi.planned_qty) > 0
				{status_clause}
				{wh_clause}
			""",
			params,
			as_dict=True,
		)
		for r in rows:
			self.add_row(
				direction=PLANNING,
				commitment_type="Production Plan",
				document_type="Production Plan",
				document=r.name,
				status=r.status,
				date=r.planned_start_date or r.posting_date,
				warehouse=r.warehouse,
				qty=flt(r.qty),
				reference="",
				remarks=_("Planned production (future demand)"),
				signed=0,
			)

	# ------------------------------------------------------------------ #
	# Source 8 - Pick Lists (allocated stock, informational)
	# ------------------------------------------------------------------ #

	def collect_pick_lists(self):
		docstatus = self._docstatus_values()
		status_clause = self._closed_clause("pl.status")
		wh_clause, params = self._wh_clause("pli.warehouse")
		params.update({"item_code": self.item_code, "company": self.company, "docstatus": docstatus})
		rows = frappe.db.sql(
			f"""
			SELECT
				pl.name,
				pl.status,
				pl.purpose,
				pli.warehouse,
				pli.sales_order,
				(pli.qty - COALESCE(pli.picked_qty, 0)) AS pending_qty,
				pli.qty
			FROM `tabPick List Item` pli
			INNER JOIN `tabPick List` pl ON pl.name = pli.parent
			WHERE pli.item_code = %(item_code)s
				AND pl.company = %(company)s
				AND pl.docstatus IN %(docstatus)s
				AND pli.qty > 0
				{status_clause}
				{wh_clause}
			""",
			params,
			as_dict=True,
		)
		for r in rows:
			self.add_row(
				direction=RESERVATION,
				commitment_type="Pick List",
				document_type="Pick List",
				document=r.name,
				status=r.status,
				date=None,
				warehouse=r.warehouse,
				qty=flt(r.qty),
				reference=(_("For SO: {0}").format(r.sales_order) if r.sales_order else (r.purpose or "")),
				remarks=_("Allocated by Pick List"),
				signed=0,  # overlaps SO/reservation - shown for traceability only
			)

	# ------------------------------------------------------------------ #
	# Source 9 - Stock Entries (submitted movements, informational)
	# ------------------------------------------------------------------ #

	def collect_stock_entries(self):
		# Submitted stock entries have already hit the ledger (so they are in
		# Actual Qty); listed here as recent context only. Draft entries appear
		# only when "Include Draft Documents" is ticked.
		params = {
			"item_code": self.item_code,
			"company": self.company,
			"docstatus": self._docstatus_values(),
			"purposes": ("Material Transfer", "Material Issue", "Manufacture"),
		}
		# A stock entry line can touch either the source or target warehouse; keep
		# the row when EITHER side is within the selected warehouse scope.
		if self.warehouses:
			wh_clause = " AND (sed.s_warehouse IN %(warehouses)s OR sed.t_warehouse IN %(warehouses)s) "
			params["warehouses"] = tuple(self.warehouses)
		else:
			wh_clause = ""
		rows = frappe.db.sql(
			f"""
			SELECT
				se.name,
				se.purpose,
				se.stock_entry_type,
				se.posting_date,
				sed.s_warehouse,
				sed.t_warehouse,
				sed.qty
			FROM `tabStock Entry Detail` sed
			INNER JOIN `tabStock Entry` se ON se.name = sed.parent
			WHERE sed.item_code = %(item_code)s
				AND se.company = %(company)s
				AND se.docstatus IN %(docstatus)s
				AND se.purpose IN %(purposes)s
				AND sed.qty > 0
				{wh_clause}
			ORDER BY se.posting_date DESC
			LIMIT 50
			""",
			params,
			as_dict=True,
		)
		for r in rows:
			# Direction from the movement's effect on the selected item's stock.
			if r.purpose == "Material Issue":
				direction = OUTGOING
			elif r.purpose == "Manufacture":
				direction = INCOMING if r.t_warehouse else OUTGOING
			else:  # Material Transfer
				direction = INCOMING if r.t_warehouse else OUTGOING
			self.add_row(
				direction=direction,
				commitment_type="Stock Entry",
				document_type="Stock Entry",
				document=r.name,
				status=r.stock_entry_type or r.purpose,
				date=r.posting_date,
				warehouse=r.t_warehouse or r.s_warehouse,
				qty=flt(r.qty),
				reference=r.stock_entry_type or r.purpose or "",
				remarks=_("Submitted {0} (already in ledger)").format(r.purpose),
				signed=0,
			)

	# ------------------------------------------------------------------ #
	# Status / closed-document filtering
	# ------------------------------------------------------------------ #

	def _closed_clause(self, status_field):
		"""Exclude closed/completed/cancelled statuses unless 'Include Closed
		Documents' is ticked. Cancelled is always excluded via docstatus, but
		Closed/Completed/Stopped are docstatus 1, so they need this guard."""
		if self.filters.get("include_closed"):
			return ""
		statuses = tuple(s for s in CLOSED_STATUSES if s != "Cancelled")
		# Inline the literal tuple; these are fixed constants, never user input.
		joined = ", ".join(frappe.db.escape(s) for s in statuses)
		return f" AND {status_field} NOT IN ({joined}) "

	# ------------------------------------------------------------------ #
	# Merge, sort, running balance
	# ------------------------------------------------------------------ #

	def sort_and_walk(self):
		far = getdate("2999-12-31")
		self.rows.sort(key=lambda r: (r.get("date") or far, r.get("priority", 99), r.get("direction") or ""))

		running = self.actual_qty
		for r in self.rows:
			signed = flt(r.pop("_signed"))
			if signed:
				running += signed
				r["running_available"] = running
			else:
				r["running_available"] = None

		# Net Available reconciles exactly with the diagnostic table.
		self.net_available = (
			self.actual_qty
			- self.reserved_total
			- self.wo_required
			+ self.wo_incoming
			+ self.po_incoming
		)

	# ------------------------------------------------------------------ #
	# Section 1 - report summary cards
	# ------------------------------------------------------------------ #

	def build_report_summary(self):
		def card(label, value, indicator=None):
			c = {"label": label, "value": value, "datatype": "Float"}
			if indicator:
				c["indicator"] = indicator
			return c

		net_ind = "Green" if self.net_available >= 0 else "Red"
		proj_ind = "Green" if self.projected_qty >= 0 else "Red"
		return [
			card(_("Actual Qty"), self.actual_qty),
			card(_("Reserved Qty"), self.reserved_total, "Orange" if self.reserved_total else None),
			card(_("Required by WO"), self.wo_required, "Orange" if self.wo_required else None),
			card(_("Incoming (WO)"), self.wo_incoming, "Green" if self.wo_incoming else None),
			card(_("Incoming (PO)"), self.po_incoming, "Green" if self.po_incoming else None),
			card(_("Projected Qty"), self.projected_qty, proj_ind),
			card(_("Net Available"), self.net_available, net_ind),
		]

	# ------------------------------------------------------------------ #
	# Section 1 + diagnostics + warnings - HTML message
	# ------------------------------------------------------------------ #

	def build_message(self):
		wh_label = self.filters.get("warehouse") or _("All Warehouses")
		position = self._inventory_position_html(wh_label)
		diagnostic = self._why_not_available_html()
		warnings = self._warnings_html()
		return f"""
			<div style="margin-bottom:12px;">
				{position}
				{diagnostic}
				{warnings}
			</div>
		"""

	def _inventory_position_html(self, wh_label):
		cells = [
			(_("Item"), self.item_code),
			(_("Item Name"), self.item_name),
			(_("Company"), self.company),
			(_("Warehouse"), wh_label),
			(_("Actual Qty"), fmt_qty(self.actual_qty)),
			(_("Reserved Qty"), fmt_qty(self.reserved_total)),
			(_("Ordered Qty (PO)"), fmt_qty(self.po_incoming)),
			(_("Planned Incoming (WO)"), fmt_qty(self.wo_incoming)),
			(_("Planned Incoming (PO)"), fmt_qty(self.po_incoming)),
			(_("Projected Qty"), fmt_qty(self.projected_qty)),
			(_("Net Available"), fmt_qty(self.net_available)),
		]
		body = "".join(
			f"<td style='padding:4px 10px;border:1px solid var(--border-color,#d1d8dd);"
			f"white-space:nowrap;'><div style='color:var(--text-muted,#8d99a6);"
			f"font-size:11px;'>{label}</div><div style='font-weight:600;'>{val}</div></td>"
			for label, val in cells
		)
		return (
			"<div style='font-weight:600;margin:4px 0;'>"
			+ _("Inventory Position")
			+ "</div><div style='overflow-x:auto;'><table style='border-collapse:collapse;"
			"margin-bottom:14px;'><tr>" + body + "</tr></table></div>"
		)

	def _why_not_available_html(self):
		def line(label, qty, sign):
			colour = "#1b8a2f" if sign > 0 else ("#b71c1c" if sign < 0 else "inherit")
			shown = ("+" if sign > 0 else ("-" if sign < 0 else "")) + fmt_qty(abs(qty))
			return (
				f"<tr><td style='padding:3px 10px;border:1px solid var(--border-color,#d1d8dd);'>{label}</td>"
				f"<td style='padding:3px 10px;border:1px solid var(--border-color,#d1d8dd);"
				f"text-align:right;color:{colour};font-variant-numeric:tabular-nums;'>{shown}</td></tr>"
			)

		rows = (
			line(_("Actual Stock"), self.actual_qty, 0)
			+ line(_("Reserved for Sales Orders"), self.reserved_so, -1 if self.reserved_so else 0)
			+ line(_("Reserved for Work Orders"), self.reserved_wo, -1 if self.reserved_wo else 0)
			+ line(_("Required by Work Orders (unreserved)"), self.wo_required, -1 if self.wo_required else 0)
			+ line(_("Incoming from Open Work Orders"), self.wo_incoming, 1 if self.wo_incoming else 0)
			+ line(_("Incoming from Purchase Orders"), self.po_incoming, 1 if self.po_incoming else 0)
		)
		net_colour = "#1b8a2f" if self.net_available >= 0 else "#b71c1c"
		net = (
			f"<tr style='font-weight:700;background:var(--subtle-fg,#f4f5f6);'>"
			f"<td style='padding:4px 10px;border:1px solid var(--border-color,#d1d8dd);'>"
			+ _("Net Available")
			+ f"</td><td style='padding:4px 10px;border:1px solid var(--border-color,#d1d8dd);"
			f"text-align:right;color:{net_colour};font-variant-numeric:tabular-nums;'>"
			f"{fmt_qty(self.net_available)}</td></tr>"
		)
		return (
			"<div style='font-weight:600;margin:4px 0;'>"
			+ _("Why Not Available? (reconciliation)")
			+ "</div><table style='border-collapse:collapse;margin-bottom:14px;min-width:340px;'>"
			+ rows
			+ net
			+ "</table>"
		)

	def _warnings_html(self):
		warnings = []
		unreserved_so = self.so_demand - self.reserved_so
		if self.so_demand > 0.0001 and self.reserved_so <= 0.0001:
			warnings.append(_("Sales Order demand exists but no Stock Reservation was found."))
		elif unreserved_so > 0.0001:
			warnings.append(
				_("Sales Order demand of {0} is not backed by a Stock Reservation.").format(
					fmt_qty(unreserved_so)
				)
			)
		if self.wo_incoming > 0.0001 and self.net_available < 0:
			warnings.append(
				_("Work Order is producing this item, but net available raw stock is negative.")
			)
		if self.reserved_total > self.actual_qty + 0.0001:
			warnings.append(
				_("Reserved quantity ({0}) exceeds actual stock ({1}).").format(
					fmt_qty(self.reserved_total), fmt_qty(self.actual_qty)
				)
			)
		if self.projected_qty < 0:
			warnings.append(_("Projected quantity is negative ({0}).").format(fmt_qty(self.projected_qty)))
		if self.net_available < 0:
			warnings.append(_("Net available quantity is negative ({0}).").format(fmt_qty(self.net_available)))

		if not warnings:
			return (
				"<div style='color:#1b8a2f;font-weight:600;'>&#10003; "
				+ _("No availability warnings.")
				+ "</div>"
			)
		items = "".join(f"<li style='margin:2px 0;'>{w}</li>" for w in warnings)
		return (
			"<div style='font-weight:600;margin:4px 0;color:#b71c1c;'>&#9888; "
			+ _("Intelligent Warnings")
			+ f"</div><ul style='margin:0 0 4px 18px;padding:0;color:#b71c1c;'>{items}</ul>"
		)

	# ------------------------------------------------------------------ #
	# Columns
	# ------------------------------------------------------------------ #

	def get_columns(self):
		return [
			{"label": _("Priority"), "fieldname": "priority", "fieldtype": "Int", "width": 70},
			{"label": _("Direction"), "fieldname": "direction", "fieldtype": "Data", "width": 105},
			{"label": _("Commitment Type"), "fieldname": "commitment_type", "fieldtype": "Data", "width": 165},
			{"label": _("Document Type"), "fieldname": "document_type", "fieldtype": "Data", "width": 130},
			# Dynamic Link resolved by the row's Document Type, so the value opens
			# the original Sales Order / Work Order / Purchase Order / ... document.
			{"label": _("Document"), "fieldname": "document", "fieldtype": "Dynamic Link", "options": "document_type", "width": 160},
			{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
			{"label": _("Date"), "fieldname": "date", "fieldtype": "Date", "width": 100},
			{"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 150},
			{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 100},
			{"label": _("Running Available"), "fieldname": "running_available", "fieldtype": "Float", "width": 140},
			{"label": _("Reference"), "fieldname": "reference", "fieldtype": "Data", "width": 200},
			{"label": _("Remarks"), "fieldname": "remarks", "fieldtype": "Data", "width": 280},
		]


def fmt_qty(value):
	"""Compact number formatting - drop the decimals when the value is whole."""
	value = flt(value)
	if value == int(value):
		return f"{int(value):,}"
	return f"{value:,.2f}"
