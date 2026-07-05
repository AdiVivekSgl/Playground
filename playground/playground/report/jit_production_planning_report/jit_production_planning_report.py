# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
JIT Production Planning Report
===============================

A twin of ERPNext's standard "Production Planning Report"
(erpnext/manufacturing/report/production_planning_report) that gets its FG
demand automatically from THIS APP's FG Stock Reservation Manager instead of
Sales Order / Work Order / Material Request:

  Order Qty (per FG item) = Sum of "Short to Complete" across that item's open
                             SO lines (FGSRM's short_to_complete)
  Available (per FG item) = Sum of "Reservable Qty" across the same lines
                             (FGSRM's reservable_now)

Both figures come straight from fg_stock_reservation_manager.execute(), which
already applies the report's filters (item/customer/date range/unreserved
basis) - so this report reuses that module's aggregation rather than
recomputing Bin/Stock Reservation Entry logic itself. FGSRM's reservable_now
is already FIFO-capped at each item's free stock (see that module), so simply
summing it per item cannot double count stock.

From there, downstream logic mirrors the standard Production Planning Report:
BOM explosion (BOM Item, or BOM Explosion Item when "Include Sub-assembly Raw
Materials" is checked) for required qty per raw material, and Bin lookups to
allot available raw-material stock - same "first row of a group carries the FG
columns, later rows blank" layout and red-highlight-on-shortage formatting as
the standard report and its manual-entry twin (Production Plan Shortage
Simulator), which this was scaffolded from.
"""

import frappe
from frappe import _
from frappe.utils import flt

from erpnext.stock.doctype.warehouse.warehouse import get_child_warehouses

from playground.playground.report.fg_stock_reservation_manager.fg_stock_reservation_manager import (
	execute as fgsrm_execute,
)


def execute(filters=None):
	return JITProductionPlanningReport(filters).execute_report()


class JITProductionPlanningReport:
	def __init__(self, filters=None):
		self.filters = frappe._dict(filters or {})
		self.raw_materials_dict = {}
		self.data = []

	def execute_report(self):
		self.get_orders_from_fgsrm()
		self.get_raw_materials()
		self.get_item_details()
		self.get_bin_details()
		self.prepare_data()
		self.get_columns()

		return self.columns, self.data

	def get_orders_from_fgsrm(self):
		"""Builds self.orders from the FG Stock Reservation Manager's own
		aggregation instead of querying Sales Order / Work Order / Material
		Request directly. Same filters (item/customer/SO/date range/unreserved
		basis) are passed straight through, so this report always matches
		whatever the FGSRM would show for the same filter set."""
		_fgsrm_columns, fgsrm_data = fgsrm_execute(self.filters)

		per_item = {}
		for row in fgsrm_data:
			item_code = row.get("item_code")
			if not item_code:
				continue
			entry = per_item.setdefault(item_code, {"order_qty": 0.0, "available_qty": 0.0})
			entry["order_qty"] += flt(row.get("short_to_complete"))
			entry["available_qty"] += flt(row.get("reservable_now"))

		self.orders = []
		skipped_no_bom = []

		for item_code, vals in per_item.items():
			if vals["order_qty"] <= 0:
				continue

			item = frappe.db.get_value(
				"Item", item_code, ["item_name", "stock_uom", "default_bom"], as_dict=True
			)
			if not item:
				continue

			bom_no = item.default_bom or frappe.db.get_value(
				"BOM",
				{"item": item_code, "is_default": 1, "is_active": 1, "docstatus": 1},
				"name",
			)
			if not bom_no:
				skipped_no_bom.append(item_code)
				continue

			self.orders.append(
				frappe._dict(
					{
						"production_item": item_code,
						"production_item_name": item.item_name,
						"qty_to_manufacture": vals["order_qty"],
						"available_qty": vals["available_qty"],
						"stock_uom": item.stock_uom,
						"bom_no": bom_no,
					}
				)
			)

		if skipped_no_bom:
			frappe.msgprint(
				_("Skipped (no active default BOM found): {0}").format(", ".join(sorted(set(skipped_no_bom)))),
				indicator="orange",
				alert=True,
			)

		if not self.orders:
			frappe.msgprint(
				_("No FG items currently have a Short to Complete quantity in the FG Stock Reservation Manager for these filters."),
				indicator="blue",
				alert=True,
			)

	def get_raw_materials(self):
		"""Same BOM/BOM Explosion Item query as the standard report's
		non-Work-Order branch - every FG row here always resolves through a BOM."""
		if not self.orders:
			return

		self.item_codes = []

		bom_nos = [d.bom_no for d in self.orders if d.bom_no]
		if not bom_nos:
			return

		bom_item_doctype = (
			"BOM Explosion Item" if self.filters.include_subassembly_raw_materials else "BOM Item"
		)

		bom = frappe.qb.DocType("BOM")
		bom_item = frappe.qb.DocType(bom_item_doctype)

		if self.filters.include_subassembly_raw_materials:
			qty_field = bom_item.qty_consumed_per_unit
		else:
			qty_field = bom_item.qty / bom.quantity

		raw_materials = (
			frappe.qb.from_(bom)
			.from_(bom_item)
			.select(
				bom_item.parent,
				bom_item.item_code,
				bom_item.item_name.as_("raw_material_name"),
				qty_field.as_("required_qty_per_unit"),
			)
			.where((bom_item.parent.isin(bom_nos)) & (bom_item.parent == bom.name) & (bom.docstatus == 1))
		).run(as_dict=True)

		if not raw_materials:
			return

		self.item_codes.extend([d.item_code for d in raw_materials])

		for d in raw_materials:
			self.raw_materials_dict.setdefault(d.parent, [])
			self.raw_materials_dict[d.parent].append(d)

	def get_item_details(self):
		if not (self.orders and self.item_codes):
			return

		self.item_details = {}
		filters = {"parent": ("in", self.item_codes)}
		if self.filters.company:
			filters["company"] = self.filters.company

		for d in frappe.get_all("Item Default", fields=["parent", "default_warehouse"], filters=filters):
			self.item_details[d.parent] = d

	def get_bin_details(self):
		"""Bin lookups for RAW MATERIAL stock only - the FG side's Available
		already comes from the FG Stock Reservation Manager, so there's no need
		to re-derive FG stock from Bin here."""
		if not (self.orders and self.raw_materials_dict):
			return

		self.bin_details = {}
		self.warehouses = []
		self.mrp_warehouses = []
		if self.filters.raw_material_warehouse:
			self.mrp_warehouses.extend(get_child_warehouses(self.filters.raw_material_warehouse))
			self.warehouses.extend(self.mrp_warehouses)

		if not self.warehouses:
			return

		for d in frappe.get_all(
			"Bin",
			fields=["warehouse", "item_code", "actual_qty"],
			filters={"item_code": ("in", self.item_codes), "warehouse": ("in", self.warehouses)},
		):
			key = (d.item_code, d.warehouse)
			self.bin_details.setdefault(key, d)

	def prepare_data(self):
		if not self.orders:
			return

		for d in self.orders:
			key = d.bom_no
			if not key or not self.raw_materials_dict.get(key):
				continue

			self.update_raw_materials(d, key)

	def update_raw_materials(self, data, key):
		self.index = 0

		for d in self.raw_materials_dict.get(key):
			d.required_qty = d.required_qty_per_unit * data.qty_to_manufacture

			warehouses = self.mrp_warehouses or []
			item_details = self.item_details.get(d.item_code) if hasattr(self, "item_details") else None
			if item_details:
				warehouses = [item_details["default_warehouse"]]

			d.remaining_qty = d.required_qty
			self.pick_materials_from_warehouses(d, data, warehouses)

	def pick_materials_from_warehouses(self, args, order_data, warehouses):
		warehouses = [w for w in warehouses if w] or [None]

		for index, warehouse in enumerate(warehouses):
			if not args.remaining_qty:
				return

			row = self.get_args()

			bin_data = self.bin_details.get((args.item_code, warehouse)) if warehouse else None

			args.allotted_qty = 0
			if bin_data and bin_data.get("actual_qty") > 0:
				args.allotted_qty = (
					bin_data.get("actual_qty")
					if (args.required_qty > bin_data.get("actual_qty"))
					else args.required_qty
				)
				args.remaining_qty -= args.allotted_qty
				bin_data["actual_qty"] -= args.allotted_qty

			if (
				self.mrp_warehouses and (args.allotted_qty or index == len(warehouses) - 1)
			) or not self.mrp_warehouses:
				if not self.index:
					row.update(order_data)
					self.index += 1

				row.update(args)
				self.data.append(row)

	def get_args(self):
		return frappe._dict(
			{
				"production_item": "",
				"qty_to_manufacture": "",
				"available_qty": "",
			}
		)

	def get_columns(self):
		self.columns = [
			{
				"label": _("Item Code"),
				"fieldname": "production_item",
				"fieldtype": "Link",
				"options": "Item",
				"width": 130,
			},
			{"label": _("Order Qty"), "fieldname": "qty_to_manufacture", "fieldtype": "Float", "width": 100},
			{"label": _("Available"), "fieldname": "available_qty", "fieldtype": "Float", "width": 100},
			{
				"label": _("Raw Material Name"),
				"fieldname": "raw_material_name",
				"fieldtype": "Data",
				"width": 200,
			},
			{"label": _("Required Qty"), "fieldname": "required_qty", "fieldtype": "Float", "width": 110},
			{"label": _("Allotted Qty"), "fieldname": "allotted_qty", "fieldtype": "Float", "width": 110},
		]
