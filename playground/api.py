import frappe


@frappe.whitelist()
def create_leave_request(leave_type=None, from_date=None, to_date=None, half_day=0):
    to_date = to_date or from_date

    if not leave_type:
        frappe.throw("Please provide the leave type.")
    if not from_date:
        frappe.throw("Please provide the start date.")

    user_email = frappe.session.user

    employee = frappe.db.get_value(
        "Employee",
        {"user_id": user_email},
        ["name", "employee_name"],
        as_dict=True,
    )

    if not employee:
        frappe.throw(f"No Employee record linked to {user_email}. Please contact HR.")

    leave_app = frappe.get_doc(
        {
            "doctype": "Leave Application",
            "employee": employee.name,
            "leave_type": leave_type,
            "from_date": from_date,
            "to_date": to_date,
            "half_day": int(half_day),
            "description": "Submitted via Raven HR Bot",
            "status": "Open",
        }
    )

    leave_app.insert(ignore_permissions=True)
    leave_app.submit()

    return (
        f"✅ Leave request {leave_app.name} submitted for "
        f"{employee.employee_name} ({leave_type}: {from_date} to {to_date})."
    )
