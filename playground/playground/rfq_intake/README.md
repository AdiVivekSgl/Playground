# RFQ Intake (email → Opportunity)

Forward a customer RFQ, or one of our own quotations, to the intake mailbox (for example `rfq@frontec.co.in`). A background job then:

1. Saves a **PDF of the email**, with its header, body, inline images and a list of attachments.
2. Keeps **every attachment** and attaches it to both the Communication and the Opportunity.
3. Uses Claude to read the email and its attachments (PDF, XLSX, DOCX, images, text) and extract **Title, Customer, Territory, Target Date and Overall Value**, plus line items and a reference number.
4. **Creates a native Opportunity**. For one of our own quotes, it **updates the matching open Opportunity** instead.
5. **Mirrors it into the Gmail-sidebar CRM layer**: a CRM Opportunity linked through `erpnext_opportunity`, a CRM Contact mapping and a CRM Activity entry.
6. Optionally **replies to the forwarder** with the summary and links.

```
Gmail intake ─IMAP─► Email Account ─► Communication + Files
                                         │ Communication.after_insert → long-queue job
                                         ▼
      email PDF ─► Claude extraction ─► Customer/Lead + Territory ─► Opportunity ─► CRM Opportunity + Activity
```

## Files
| File | Role |
|---|---|
| `handler.py` | Hook entry point (`on_email`), the background job (`process`), and the `reprocess` whitelisted method |
| `email_pdf.py` + `templates/email_pdf.html` | Renders the Communication to a PDF with wkhtmltopdf. Private inline images are embedded as data URIs. |
| `extract.py` | The Claude call. The output is forced into a JSON schema. The territory must come from the leaf Territory list. |
| `erp_mapping.py` | Customer/Lead resolution (reusing `playground.gmail` lookups), territory, currency, and create/update of the Opportunity |
| `crm_link.py` | Creates or updates the CRM Opportunity, CRM Contact and CRM Activity |
| `notify.py` | Sends the summary reply to the forwarder |
| `utils.py` | Pure helpers (fingerprint, fuzzy match, XLSX/DOCX to text). Unit-tested without a site. |
| `setup.py` | `after_migrate`: creates the "Email Intake" custom fields on Opportunity |

## Setup
1. Install the SDK in the bench environment:
   ```bash
   bench pip install anthropic
   ```
2. Run `bench migrate`. This creates the **RFQ Intake Settings** doctype and the Opportunity fields.
3. Create an **Email Account** for the intake mailbox:
   - GSuite/Gmail with OAuth, **Enable Incoming** ticked.
   - Leave **Append To** empty.
   - Untick "Create Contacts from Incoming Emails".
4. Fill in **RFQ Intake Settings**:
   - Tick Enabled, then set the Intake Email Account, Company, Default Territory and Default Currency.
   - Internal Domains defaults to `frontec.co.in`.
   - Enter the Anthropic API key.
   - Model defaults to `claude-opus-5`. You can switch it to `claude-sonnet-5` to cut cost.
5. Make sure the Territory tree has **leaf** territories. The AI may only pick from those.

## Decision logic
| Question | Logic |
|---|---|
| Customer | Claude reads the *forwarded* header block, because the forwarder is not the customer. ERPNext then tries, in order: (1) an exact Contact email → Customer; (2) the one Customer behind that business domain, skipping public and internal domains; (3) a fuzzy name match (≥ 0.85); (4) an existing or new Lead. |
| Territory | The Customer's territory if it is set, otherwise the AI's pick from the leaf territories, otherwise the default. |
| Target date | For an RFQ: the submission deadline, otherwise the required delivery date. For our quote: the validity date or decision date. |
| Value | The stated total or budget excluding tax. Otherwise qty × rate summed over line items. Otherwise empty, and the Opportunity is flagged for review. The AI never guesses a value. |
| Our quote | Finds an open Opportunity for the party: first by reference number, then by title similarity, then the only open one. It updates the amount and date, and sets the status to Quotation and the CRM stage to Quotation. The CRM stage only moves forward. |
| Duplicates | A fingerprint made from the cleaned subject and attachment hashes (or the body, when there are no attachments). If the same RFQ is forwarded again, the new email is linked and nothing new is created. |
| Needs Review | Set when confidence is below the threshold, the customer is a new Lead, the value is missing, or no customer name was found. |

If the CRM-layer sync fails, the change is rolled back to a savepoint and logged. The native Opportunity is kept.

## Failures and re-runs
- Extraction errors go to the **Error Log**, and a comment is added to the Communication. The email PDF is still saved.
- To retry, call `playground.playground.rfq_intake.handler.reprocess` with `communication=<name>` as a System Manager.
- Attachments that could not be read are listed in a comment on the Opportunity: unsupported types, files over the 20 MB total, and legacy `.xls`/`.doc`.

## Tests
```bash
bench --site <site> run-tests --module playground.playground.rfq_intake.tests.test_utils
```

## Privacy
The email body and attachments are sent to the Anthropic API.
