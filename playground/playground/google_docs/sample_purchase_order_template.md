# Sample Purchase Order — Google Docs template

Create a Google Doc, style it with your company header/footer/logo, and paste the
body below. Keep every `{{token}}` as **plain, unformatted text**, and keep
`{{items_table}}` **alone on its own line**.

---

```text
PURCHASE ORDER


Purchase Order No:  {{document_number}}
Date:               {{transaction_date}}


Supplier
{{supplier_name}}
{{supplier_address}}


Items

{{items_table}}


Grand Total:  {{grand_total}}


Terms & Conditions

{{terms}}
```

---

Notes:

* `{{document_number}}` maps to the PO's `name`, `{{transaction_date}}` to its
  date, etc. — see `sample_template_config.md`.
* Everything outside the tokens (headings, spacing, your logo/header/footer) is
  preserved exactly, because the engine copies this template and edits only the
  tokens.
* Anything you or a colleague type into the *generated* doc later (e.g. under
  Terms & Conditions) is left untouched by **Update Google Doc**.
