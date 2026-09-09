# Copyright (c) 2026, Frontec and contributors
# For license information, please see license.txt

"""
ERP → editable Google Docs rendering engine.

A configuration-driven service layer that renders a Frappe document into a copy
of a Google Docs *template*, replacing placeholders and rendering child tables,
then stores the resulting Google Doc id/URL back on the ERP document. ERPNext
stays the system of record; the Google Doc is the editable presentation layer.

Public server API (see api.py, all `@frappe.whitelist`)::

    create_google_document(doctype, name)
    update_google_document(doctype, name)
    get_google_document(doctype, name)

Layering (nothing above talks to Google directly except the two api wrappers):

    api.py         orchestration + whitelisted endpoints + background enqueue
    renderer.py    Frappe doc  ->  ordered Google Docs batchUpdate requests
    docs.py        thin Google Docs API client (documents.get / batchUpdate)
    drive.py       thin Google Drive API client (copy / move / rename / share)
    auth.py        service-account credentials -> authorised API clients
    formatting.py  field-type-aware value formatting
    placeholders.py placeholder + managed-section (named-range) helpers
    permissions.py  Frappe permission checks + Google sharing
    setup.py       after_migrate: metadata custom fields + role
"""
