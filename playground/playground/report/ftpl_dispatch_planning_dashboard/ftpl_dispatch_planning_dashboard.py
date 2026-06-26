# Copyright (c) 2026, Playground
# License: MIT. See license.txt

"""FTPL Dispatch Planning Dashboard script report.

The report intentionally returns one row per Sales Order Item.  Expensive data is
loaded in a handful of set-based queries and then merged in Python dictionaries to
avoid N+1 lookups when thousands of Sales Order Items are in scope.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


READY = "Ready to Dispatch"
PARTIAL = "Partially Ready"
AWAITING = "Awaiting Production"
REQUIRED = "Production Required"


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()

    sales_rows = get_sales_order_items(filters)
    if not sales_rows:
        return columns, []

    item_codes = {row.item_code for row in sales_rows if row.item_code}
    so_names = {row.sales_order for row in sales_rows if row.sales_order}
    warehouse = filters.get("warehouse") or "Stores - FTPL"

    stock_by_item = get_stock(item_codes, warehouse)
    reservation_by_so_item, total_reserved_by_item = get_reservations(so_names, item_codes, warehouse)
    work_orders = get_work_orders(so_names, item_codes)

    data = build_rows(
        sales_rows=sales_rows,
        stock_by_item=stock_by_item,
        reservation_by_so_item=reservation_by_so_item,
        total_reserved_by_item=total_reserved_by_item,
        work_orders=work_orders,
    )

    data = apply_python_filters(data, filters)
    return columns, data


def get_columns():
    """Return columns in the same order as the business dashboard sections."""
    return [
        {"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 190},
        {"label": _("Sales Order"), "fieldname": "sales_order", "fieldtype": "Link", "options": "Sales Order", "width": 150},
        {"label": _("SO Date"), "fieldname": "so_date", "fieldtype": "Date", "width": 105},
        {"label": _("Updated Delivery Date"), "fieldname": "updated_delivery_date", "fieldtype": "Date", "width": 145},
        {"label": _("Customer Purchase Order No"), "fieldname": "customer_po_no", "fieldtype": "Data", "width": 190},
        {"label": _("Sales Person"), "fieldname": "sales_person", "fieldtype": "Data", "width": 160},
        {"label": _("Item Code"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
        {"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
        {"label": _("Item Group"), "fieldname": "item_group", "fieldtype": "Link", "options": "Item Group", "width": 130},
        {"label": _("Description"), "fieldname": "description", "fieldtype": "Data", "width": 220},
        {"label": _("Ordered Qty"), "fieldname": "ordered_qty", "fieldtype": "Float", "width": 110},
        {"label": _("Delivered Qty"), "fieldname": "delivered_qty", "fieldtype": "Float", "width": 115},
        {"label": _("Pending Qty"), "fieldname": "pending_qty", "fieldtype": "Float", "width": 110},
        {"label": _("Pending Value"), "fieldname": "pending_value", "fieldtype": "Currency", "options": "currency", "width": 125},
        {"label": _("Reserved Against SO"), "fieldname": "reserved_qty", "fieldtype": "Float", "width": 145},
        {"label": _("Reservation %"), "fieldname": "reservation_percent", "fieldtype": "Percent", "width": 120},
        {"label": _("Stores Stock"), "fieldname": "stores_stock", "fieldtype": "Float", "width": 110},
        {"label": _("Free Stock"), "fieldname": "free_stock", "fieldtype": "Float", "width": 105},
        {"label": _("Shortage"), "fieldname": "shortage", "fieldtype": "Float", "width": 100},
        {"label": _("Work Order"), "fieldname": "work_order", "fieldtype": "Link", "options": "Work Order", "width": 145},
        {"label": _("WO Status"), "fieldname": "wo_status", "fieldtype": "Data", "width": 120},
        {"label": _("Planned Start Date"), "fieldname": "planned_start_date", "fieldtype": "Datetime", "width": 160},
        {"label": _("Planned End Date"), "fieldname": "planned_end_date", "fieldtype": "Datetime", "width": 160},
        {"label": _("Material Availability %"), "fieldname": "material_availability_percent", "fieldtype": "Percent", "width": 165},
        {"label": _("Production Stage"), "fieldname": "production_stage", "fieldtype": "Data", "width": 140},
        {"label": _("Dispatch Readiness"), "fieldname": "dispatch_readiness", "fieldtype": "Data", "width": 160},
    ]


def get_sales_order_items(filters):
    conditions = [
        "so.docstatus = 1",
        "so.status not in ('Closed', 'On Hold')",
        "soi.docstatus = 1",
        "(soi.qty - ifnull(soi.delivered_qty, 0)) > 0",
    ]
    values = {}

    if filters.get("company"):
        conditions.append("so.company = %(company)s")
        values["company"] = filters.company
    add_multiselect_condition(conditions, values, "so.customer", "customers", filters.get("customer"))
    add_multiselect_condition(conditions, values, "so.name", "sales_orders", filters.get("sales_orders"))
    add_multiselect_condition(conditions, values, "soi.item_code", "items", filters.get("item"))

    if filters.get("item_group"):
        conditions.append("soi.item_group = %(item_group)s")
        values["item_group"] = filters.item_group
    if filters.get("updated_delivery_date_from"):
        conditions.append("soi.custom_updated_delivery_date >= %(delivery_from)s")
        values["delivery_from"] = filters.updated_delivery_date_from
    if filters.get("updated_delivery_date_to"):
        conditions.append("soi.custom_updated_delivery_date <= %(delivery_to)s")
        values["delivery_to"] = filters.updated_delivery_date_to
    if filters.get("show_only_overdue"):
        conditions.append("soi.custom_updated_delivery_date < %(today)s")
        values["today"] = nowdate()

    return frappe.db.sql(
        f"""
        select
            so.name as sales_order,
            so.transaction_date as so_date,
            so.customer,
            so.po_no as customer_po_no,
            so.currency,
            soi.name as sales_order_item,
            soi.item_code,
            soi.item_name,
            soi.item_group,
            soi.description,
            soi.qty as ordered_qty,
            ifnull(soi.delivered_qty, 0) as delivered_qty,
            soi.rate,
            soi.custom_updated_delivery_date as updated_delivery_date,
            group_concat(distinct st.sales_person order by st.idx separator ', ') as sales_person
        from `tabSales Order Item` soi
        inner join `tabSales Order` so on so.name = soi.parent
        left join `tabSales Team` st on st.parenttype = 'Sales Order' and st.parent = so.name
        where {' and '.join(conditions)}
        group by soi.name
        order by soi.custom_updated_delivery_date asc, so.transaction_date asc, so.name asc, soi.idx asc
        """,
        values,
        as_dict=True,
    )


def add_multiselect_condition(conditions, values, field, key, raw_value):
    selected = normalize_multiselect(raw_value)
    if selected:
        conditions.append(f"{field} in %({key})s")
        values[key] = tuple(selected)


def normalize_multiselect(value):
    if not value:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [item for item in value if item]


def get_stock(item_codes, warehouse):
    if not item_codes:
        return {}
    rows = frappe.db.sql(
        """
        select item_code, actual_qty
        from `tabBin`
        where warehouse = %(warehouse)s and item_code in %(item_codes)s
        """,
        {"warehouse": warehouse, "item_codes": tuple(item_codes)},
        as_dict=True,
    )
    return {row.item_code: flt(row.actual_qty) for row in rows}


def get_reservations(so_names, item_codes, warehouse):
    if not so_names or not item_codes:
        return {}, {}
    rows = frappe.db.sql(
        """
        select voucher_no as sales_order, item_code, sum(ifnull(reserved_qty, 0)) as reserved_qty
        from `tabStock Reservation Entry`
        where docstatus = 1
            and warehouse = %(warehouse)s
            and voucher_no in %(so_names)s
            and item_code in %(item_codes)s
        group by voucher_no, item_code
        """,
        {"warehouse": warehouse, "so_names": tuple(so_names), "item_codes": tuple(item_codes)},
        as_dict=True,
    )

    by_so_item = {}
    by_item = {}
    for row in rows:
        qty = flt(row.reserved_qty)
        by_so_item[(row.sales_order, row.item_code)] = qty
        by_item[row.item_code] = by_item.get(row.item_code, 0) + qty
    return by_so_item, by_item


def get_work_orders(so_names, item_codes):
    if not so_names or not item_codes:
        return {}

    # Some ERPNext installations do not have a percentage field for material
    # availability. Select the standard status field only when it exists.
    material_field = ""
    if frappe.db.has_column("Work Order", "material_availability_status"):
        material_field = ", material_availability_status"

    rows = frappe.db.sql(
        f"""
        select
            name,
            sales_order,
            production_item as item_code,
            status,
            planned_start_date,
            planned_end_date
            {material_field}
        from `tabWork Order`
        where docstatus < 2
            and sales_order in %(so_names)s
            and production_item in %(item_codes)s
        order by modified desc
        """,
        {"so_names": tuple(so_names), "item_codes": tuple(item_codes)},
        as_dict=True,
    )

    work_orders = {}
    for row in rows:
        # Keep the most recently modified work order for each SO + Item pair.
        work_orders.setdefault((row.sales_order, row.item_code), row)
    return work_orders


def build_rows(sales_rows, stock_by_item, reservation_by_so_item, total_reserved_by_item, work_orders):
    data = []
    for row in sales_rows:
        pending_qty = max(flt(row.ordered_qty) - flt(row.delivered_qty), 0)
        reserved_qty = flt(reservation_by_so_item.get((row.sales_order, row.item_code), 0))
        stores_stock = flt(stock_by_item.get(row.item_code, 0))
        free_stock = max(stores_stock - flt(total_reserved_by_item.get(row.item_code, 0)), 0)
        shortage = max(pending_qty - reserved_qty, 0)
        work_order = work_orders.get((row.sales_order, row.item_code))

        data.append(
            {
                "customer": row.customer,
                "sales_order": row.sales_order,
                "so_date": row.so_date,
                "updated_delivery_date": row.updated_delivery_date,
                "customer_po_no": row.customer_po_no,
                "sales_person": row.sales_person,
                "item_code": row.item_code,
                "item_name": row.item_name,
                "item_group": row.item_group,
                "description": row.description,
                "ordered_qty": flt(row.ordered_qty),
                "delivered_qty": flt(row.delivered_qty),
                "pending_qty": pending_qty,
                "pending_value": pending_qty * flt(row.rate),
                "currency": row.currency,
                "reserved_qty": reserved_qty,
                "reservation_percent": round((reserved_qty / pending_qty * 100), 1) if pending_qty else 0,
                "stores_stock": stores_stock,
                "free_stock": free_stock,
                "shortage": shortage,
                "work_order": work_order.name if work_order else None,
                "wo_status": work_order.status if work_order else None,
                "planned_start_date": work_order.planned_start_date if work_order else None,
                "planned_end_date": work_order.planned_end_date if work_order else None,
                "material_availability_percent": get_material_availability_percent(work_order),
                "production_stage": get_production_stage(work_order),
                "dispatch_readiness": get_dispatch_readiness(pending_qty, reserved_qty, work_order),
            }
        )
    return data


def get_material_availability_percent(work_order):
    """ERPNext stores availability as status text, not always as a percentage."""
    if not work_order:
        return None
    status = (work_order.get("material_availability_status") or "").lower()
    if status in {"available", "fully available"}:
        return 100
    if status in {"not available", "unavailable"}:
        return 0
    return None


def get_production_stage(work_order):
    if not work_order:
        return "Not Planned"
    status = work_order.status or "Draft"
    stage_map = {
        "Draft": "Draft",
        "Not Started": "Material Reserved",
        "In Process": "In Process",
        "Completed": "Completed",
        "Stopped": "Stopped",
        "Closed": "Completed",
    }
    return stage_map.get(status, status)


def get_dispatch_readiness(pending_qty, reserved_qty, work_order):
    if pending_qty <= reserved_qty:
        return READY
    if reserved_qty > 0:
        return PARTIAL
    if work_order:
        return AWAITING
    return REQUIRED


def apply_python_filters(data, filters):
    if filters.get("show_only_shortages"):
        data = [row for row in data if flt(row.get("pending_qty")) > flt(row.get("reserved_qty"))]
    if filters.get("show_only_unreserved"):
        data = [row for row in data if flt(row.get("reserved_qty")) == 0]
    if filters.get("show_only_overdue"):
        today = getdate(nowdate())
        data = [row for row in data if row.get("updated_delivery_date") and getdate(row.get("updated_delivery_date")) < today]
    return data
