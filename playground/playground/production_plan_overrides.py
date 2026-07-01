"""
Monkey-patch erpnext.manufacturing.doctype.production_plan.production_plan
.get_items_for_material_requests so that rows with Planned/Required Qty = 0:

  1. never throw and block calculation for the rest of the plan, and
  2. still appear explicitly in the Material Request Plan Items table as a
     zero-quantity row (rather than being silently absent), so it's clear
     the item was checked and simply needs no raw materials right now.

Kept as a flat sibling module of the app package (rather than a nested
`overrides/` subpackage) so build/packaging steps that don't reliably pick
up brand-new subdirectories can't leave it out of a deploy.
"""
import json

import frappe
from frappe.utils import flt

import erpnext.manufacturing.doctype.production_plan.production_plan as _pp_module

_original_get_items_for_material_requests = _pp_module.get_items_for_material_requests


def get_items_for_material_requests(doc, warehouses=None, get_parent_warehouse_data=None):
    if isinstance(doc, str):
        doc = frappe._dict(json.loads(doc))

    key = "po_items" if doc.get("po_items") else "items"
    rows = doc.get(key) or []

    zero_qty_rows = [
        row for row in rows
        if not flt(row.get("required_qty") or row.get("planned_qty"))
    ]
    nonzero_rows = [
        row for row in rows
        if flt(row.get("required_qty") or row.get("planned_qty"))
    ]

    # Core calculation only ever needs to run on rows with a real quantity —
    # this drives the actual shortage / MR requirement numbers, unchanged.
    doc[key] = nonzero_rows
    mr_items = _original_get_items_for_material_requests(
        doc, warehouses=warehouses, get_parent_warehouse_data=get_parent_warehouse_data
    ) or []

    # Add a visible zero-qty placeholder for every excluded row, so it's
    # explicitly listed rather than silently missing.
    for row in zero_qty_rows:
        mr_items.append(_build_zero_qty_placeholder(doc, row))

    return mr_items


def _build_zero_qty_placeholder(doc, row):
    item_code = row.get("item_code")
    item_master = frappe.get_cached_value(
        "Item",
        item_code,
        ["item_name", "stock_uom", "purchase_uom", "default_material_request_type"],
        as_dict=True,
    ) or frappe._dict()

    warehouse = (
        doc.get("for_warehouse")
        or row.get("source_warehouse")
        or row.get("default_warehouse")
        or item_master.get("default_warehouse")
    )

    return {
        "item_code": item_code,
        "item_name": row.get("item_name") or item_master.get("item_name"),
        "quantity": 0,
        "conversion_factor": 1.0,
        "required_bom_qty": 0,
        "stock_uom": row.get("stock_uom") or item_master.get("stock_uom"),
        "warehouse": warehouse,
        "safety_stock": row.get("safety_stock") or 0,
        "actual_qty": 0,
        "projected_qty": 0,
        "ordered_qty": 0,
        "reserved_qty_for_production": 0,
        "min_order_qty": row.get("min_order_qty") or 0,
        "material_request_type": row.get("default_material_request_type")
        or item_master.get("default_material_request_type"),
        "sales_order": row.get("sales_order"),
        "description": row.get("description"),
        "uom": row.get("purchase_uom") or item_master.get("purchase_uom") or item_master.get("stock_uom"),
        "main_item_code": row.get("main_bom_item"),
    }


_pp_module.get_items_for_material_requests = get_items_for_material_requests
