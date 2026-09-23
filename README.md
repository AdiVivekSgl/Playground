# Playground

Custom API scripts for ERPNext.

## Active API

- `playground.api.create_leave_request`: whitelisted Frappe endpoint for creating leave requests from the current session user.

## Modules

- **ERP → Editable Google Docs** (`playground/playground/google_docs/`): a
  configuration-driven engine that renders ERP documents into editable Google
  Docs from a Google Docs template. See its
  [README](playground/playground/google_docs/README.md) for setup and usage.
- **RFQ Intake** (`playground/playground/rfq_intake/`): forward RFQs or our own
  quotations to an intake mailbox. Each email gets a PDF copy, its attachments are
  saved, and Claude extracts Title / Customer / Territory / Target Date / Value. The
  result is a native Opportunity plus a linked CRM Opportunity for the Gmail sidebar.
  See its [README](playground/playground/rfq_intake/README.md).
