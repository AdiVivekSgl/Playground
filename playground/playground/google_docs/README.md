# ERP → Editable Google Docs

A configuration-driven engine that renders a Frappe/ERPNext document into a copy
of a **Google Docs template**, replacing placeholders and rendering child tables,
then stores the resulting Google Doc id/URL back on the ERP document.

ERPNext stays the system of record; the Google Doc is the editable presentation
layer (formatting, collaboration, comments, manual additions). V1 is **ERP →
Google Docs only** — it never reads arbitrary Google edits back into ERP.

---

## Contents

1. [How it works](#how-it-works)
2. [Installation](#installation)
3. [Google setup (service account)](#google-setup-service-account)
4. [Administrator configuration](#administrator-configuration)
5. [Building a Google Docs template](#building-a-google-docs-template)
6. [Using it (Purchase Order walkthrough)](#using-it-purchase-order-walkthrough)
7. [Enabling another DocType](#enabling-another-doctype)
8. [How updates preserve user edits](#how-updates-preserve-user-edits)
9. [Server API](#server-api)
10. [Troubleshooting](#troubleshooting)
11. [Known limitations](#known-limitations)

---

## How it works

```
ERP document ──▶ Google Document Template ──▶ copy template into Drive folder
                                            ──▶ replace {{field}} placeholders
                                            ──▶ render {{table}} markers as tables
                                            ──▶ store doc id + URL on the ERP doc
```

Code layout (`playground/playground/google_docs/`):

| File            | Responsibility |
| --------------- | -------------- |
| `api.py`        | Whitelisted endpoints, orchestration, background enqueue, logging |
| `renderer.py`   | Frappe doc → ordered Google Docs `batchUpdate` requests |
| `docs.py`       | Google Docs v1 client + document-structure reading/request builders |
| `drive.py`      | Google Drive v3 client (copy / move / rename / share) |
| `auth.py`       | Service-account credentials → authorised clients |
| `formatting.py` | Field-type-aware value formatting |
| `placeholders.py` | Placeholder / named-range / file-naming helpers |
| `permissions.py`  | Frappe permission checks + sharing domain |
| `setup.py`      | `after_migrate`: metadata custom fields |

DocTypes (under the existing **Playground** module):

* **Google Docs Settings** (Single) — credentials + defaults
* **Google Document Template** — per-DocType mapping config, with child tables
  **Google Document Field Mapping**, **Google Document Table Mapping**,
  **Google Document Column Mapping**
* **Google Document Log** — audit trail

---

## Installation

The engine ships inside the `playground` app; enabling it is just a migrate plus
the Google client libraries.

```bash
# 1. Install the Google API client libraries into the bench environment
bench pip install google-api-python-client google-auth

# 2. Apply the new DocTypes, custom fields and fixtures
bench --site <your-site> migrate

# 3. Rebuild client assets (for the form buttons)
bench build --app playground
bench --site <your-site> clear-cache
```

`bench migrate` creates the DocTypes and — via `after_migrate` — the five
**Google Document** metadata fields on every DocType that has an enabled
template. No standard ERPNext DocType JSON is edited.

---

## Google setup (service account)

V1 authenticates as a **Google Workspace service account**, optionally with
**domain-wide delegation** so generated files are owned by a real user / land in
a Shared Drive. Do this once, in the [Google Cloud Console](https://console.cloud.google.com/).

1. **Create / pick a project.**
2. **Enable APIs:** APIs & Services → Library → enable **Google Drive API** and
   **Google Docs API**.
3. **Create a service account:** IAM & Admin → Service Accounts → Create. Give it
   a name (e.g. `erpnext-google-docs`). No project roles are required.
4. **Create a JSON key:** open the service account → Keys → Add key → JSON.
   Download it — this is what you paste into ERPNext.
5. **Domain-wide delegation (recommended):**
   * On the service account, enable *"Enable Google Workspace Domain-wide
     Delegation"* and note its **Client ID**.
   * In the [Admin console](https://admin.google.com) → Security → Access and data
     control → API controls → **Domain-wide delegation** → Add new. Enter the
     Client ID and these scopes (comma-separated):
     ```
     https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/drive
     ```
6. **Share the template + folder:** share your template doc and the destination
   Drive folder with the impersonated user (or, without delegation, with the
   service account's own email — but note a bare service account has no Drive
   quota, so use a **Shared Drive** in that case).

> **Why full `drive` scope?** `drive.file` only grants access to files the app
> itself created, which cannot copy a pre-existing template. The engine needs to
> copy your template, so it requests full `drive`.

---

## Administrator configuration

Open **Google Docs Settings** (search it in the awesome bar) and fill in:

| Field | Value |
| ----- | ----- |
| Service Account JSON Key | Paste the entire downloaded JSON. Stored **encrypted**; never sent to the browser. |
| Impersonate User | The Workspace user to act as, e.g. `docs@yourcompany.com`. Required unless you generate into a Shared Drive. |
| Default Drive Folder ID | Fallback destination folder id (from the folder URL `.../folders/<ID>`). |
| Shared Drive ID | Only if the folder lives in a Shared Drive. |
| Default Sharing Mode | `Inherit Folder` by default. Never defaults to public. |

Click **Test Connection** to verify credentials + delegation before configuring
any template.

---

## Building a Google Docs template

Create a normal Google Doc (owned by your Workspace). Style it however you like —
fonts, header, footer, logo, page setup are **all preserved**, because the engine
*copies* the template and only edits placeholders. Insert plain-text tokens:

```text
PURCHASE ORDER

Purchase Order No: {{document_number}}
Date: {{transaction_date}}

Supplier:
{{supplier_name}}

Items:

{{items_table}}

Grand Total: {{grand_total}}

Terms & Conditions

{{terms}}
```

Rules:

* **Scalar placeholders** are `{{token}}`. Keep them as plain, unformatted text
  (do not bold/colour half a placeholder) so they live in a single text run.
* **Table markers** (e.g. `{{items_table}}`) must sit **on their own line**. The
  marker line is replaced by a generated Google Docs table.

A ready-to-paste example is in
[`sample_purchase_order_template.md`](sample_purchase_order_template.md), and a
matching Template configuration in
[`sample_template_config.md`](sample_template_config.md).

Then create a **Google Document Template** record:

* **Reference DocType:** `Purchase Order`
* **Google Template URL / ID:** paste the template doc URL (the id is extracted).
* **Google Drive Folder ID:** destination folder.
* **File Naming Pattern:** e.g. `{{doctype}} - {{name}} - {{supplier_name}}`.
* **Field Mappings, Tables, Table Columns:** see the sample config.

Saving an **enabled** template provisions the metadata fields on the reference
DocType immediately.

---

## Using it (Purchase Order walkthrough)

1. Open a submitted Purchase Order.
2. **Google Doc → Create Google Doc.** A copy of the template is created,
   populated, and its link stored on the PO (see the collapsible *Google
   Document* section / the dashboard banner).
3. **Google Doc → Open Google Doc** opens it in a new tab.
4. Edit text by hand in the Google Doc (add notes, comments…).
5. Change a quantity on the PO in ERP.
6. **Google Doc → Update Google Doc.** ERP-controlled fields and the item table
   are rewritten; your manual notes are left intact.
7. Repeated **Create** is refused once a doc exists — use **Update** or **Create
   New Google Doc**. No accidental duplicates.

---

## Enabling another DocType

By design this is **configuration, not code**:

1. Create a **Google Document Template** for the new DocType (Sales Order,
   Quotation, Delivery Note, …) with its field/table/column mappings.
2. Add **one line** to `hooks.py → doctype_js` pointing the DocType at the shared
   button file (no new Python, no new JS):

   ```python
   doctype_js = {
       ...
       "Sales Order": "public/js/google_docs_button.js",
   }
   ```

3. `bench build --app playground && bench --site <site> clear-cache`.

The metadata fields are auto-provisioned when the template is enabled (and again
on every migrate).

---

## How updates preserve user edits

Every piece of ERP-controlled content the engine writes is wrapped in a Google
Docs **named range** (`erpgdoc:field:<fieldname>` for scalars,
`erpgdoc:table:<fieldname>` for tables). On **Update**, the engine reads the
document, finds those named ranges, and rewrites **only** their content — so
notes, comments and text a user typed elsewhere survive untouched. If a named
range is missing (e.g. a mapping added after the doc was created), it falls back
to the literal placeholder still in the template copy.

---

## Server API

All whitelisted, permission-checked, reusable from buttons, server scripts,
workflows and scheduled jobs:

```python
from playground.playground.google_docs import api

api.create_google_document("Purchase Order", "KPX-PO-00045")           # sync
api.create_google_document("Purchase Order", "KPX-PO-00045", enqueue=1) # background
api.update_google_document("Purchase Order", "KPX-PO-00045")
api.get_google_document("Purchase Order", "KPX-PO-00045")               # read-only status
api.open_google_document("Purchase Order", "KPX-PO-00045")             # returns URL, logs "Opened"
```

**Automatic-on-submit** is architecturally supported but off by default. To turn
it on for a template whose *Creation* is `Automatic on Submit`, wire the hook:

```python
doc_events = {
    "Purchase Order": {"on_submit": "playground.playground.google_docs.api.maybe_auto_create"},
}
```

It enqueues generation and never blocks submit on a Google error.

---

## Troubleshooting

* **Errors are always surfaced.** A failed create/update flags the ERP document's
  *Google Document Status* = `Error`, writes a **Google Document Log** row
  (Action = `Failed`) with the technical detail, and the full traceback goes to
  the Frappe **Error Log**. The ERP doc never looks successful on failure.
* **Test Connection fails** → check the JSON key, that both APIs are enabled, and
  that delegation scopes are authorised in the Admin console.
* **"Required placeholder not found"** → the template is missing that `{{token}}`,
  or it is split by mid-word formatting; retype it as plain text.
* **Slow generation** → use `enqueue=1` / a background-enabled template.

---

## Known limitations (V1)

* One-way only: ERP → Google Docs. Arbitrary Google edits are not read back.
* A placeholder split across text runs by mid-word formatting is not matched —
  keep placeholders plain.
* Generated table cells use the document's default table style (no programmatic
  header bolding in V1); restyle in the template's rendered output if desired.
* A field left empty at *create* time gets no managed named range, so it cannot
  later be filled by *update* until the doc is regenerated.
