import frappe

@frappe.whitelist()
def hello():
    return {"status": "ok", "message": "Playground API is live"}
