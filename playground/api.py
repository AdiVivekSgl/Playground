import frappe

@frappe.whitelist()
def create_leave_request(leave_type="Casual Leave", from_date=None, to_date=None, half_day=0):
    
    to_date = to_date or from_date

    if not from_date:
        frappe.throw("Please provide the start date.")

    # Fetch employee from session
    user_email = frappe.session.user
    employee = frappe.db.get_value(
        "Employee",
        {"user_id": user_email},
        ["name", "employee_name", "company", "holiday_list"],
        as_dict=True
    )

    if not employee:
        frappe.throw(
            f"No Employee record linked to {user_email}. "
            "Please contact HR."
        )

    leave_app = frappe.get_doc({
        "doctype": "Leave Application",
        "employee": employee.name,
        "company": employee.company or "Frontier Technologies Pvt Ltd",
        "leave_type": leave_type,
        "from_date": from_date,
        "to_date": to_date,
        "half_day": int(half_day),
        "description": "Submitted via Raven HR Bot",
        "status": "Open"
    })

    leave_app.insert(ignore_permissions=True)
    leave_app.submit()

    return (
        f"✅ Leave request *{leave_app.name}* submitted!\n\n"
        f"👤 *Employee:* {employee.employee_name}\n"
        f"🏢 *Company:* {employee.company}\n"
        f"🏖️ *Leave Type:* {leave_type}\n"
        f"📅 *From:* {from_date}\n"
        f"📅 *To:* {to_date}"
    )
