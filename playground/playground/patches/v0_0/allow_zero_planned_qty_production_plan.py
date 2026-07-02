# playground/playground/patches/v0_0/allow_zero_planned_qty_production_plan.py
import frappe


def execute():
    frappe.make_property_setter(
        {
            "doctype": "Production Plan Item",
            "fieldname": "planned_qty",
            "property": "reqd",
            "value": 0,
            "property_type": "Check",
        },
        validate_fields_for_doctype=False,
    )
