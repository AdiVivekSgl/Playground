# Finished Kit Label Print Agent

Local Windows agent that prints finished-kit labels to a **TSC TTP-244 Pro** by
polling ERPNext (Frappe Cloud) for pending **Label Print Request** rows and
sending raw **TSPL** to the printer via `win32print`. No BarTender, no driver
rendering.

```
ERPNext (Frappe Cloud)
      │   Label Print Request (Status = Pending)
      ▼
label_print_agent.py  (this PC)
      │   RAW TSPL via win32print
      ▼
TSC TTP-244 Pro
```

The Work Order side is handled by the `playground` app: when a Work Order reaches
**In Process**, it auto-creates a `Label Print Request`. This agent only prints
and reports back. It never touches Work Orders.

## 1. Prerequisites

- Windows PC with the TSC TTP-244 Pro installed (note its exact **printer name**
  from *Settings → Printers & scanners* — it must match `printer_name` in the
  config).
- Python 3.9+.
- Dependencies:

```bash
pip install pywin32 requests
```

## 2. Create a scoped API user in ERPNext

Do this once, in ERPNext, as an Administrator:

1. **Create the role** (the app also creates it on migrate): a role named
   `Label Printer` with Desk access.
2. **Create a user** e.g. `label.printer@your-company.com`.
   - Give it **only** the `Label Printer` role (plus the default roles a user must
     have). It does not need System Manager.
3. Confirm the `Label Print Request` DocType grants `Label Printer` read + write
   (it does by default — see the app's DocType permissions).
4. On that user, open **Settings → API Access** and click **Generate Keys**.
   Copy the **API Key** and **API Secret**.

> The agent authenticates with `Authorization: token <api_key>:<api_secret>` over
> HTTPS. Keep the secret out of version control.

## 3. Configure the agent

```bash
copy config.example.json config.json
```

Edit `config.json`:

| Key                     | Meaning                                                        |
| ----------------------- | ------------------------------------------------------------- |
| `site_url`              | Your ERPNext base URL, e.g. `https://your-site.frappe.cloud`.  |
| `api_key` / `api_secret`| From step 2.                                                   |
| `printer_name`          | Exact Windows printer name of the TSC TTP-244 Pro.            |
| `poll_interval_seconds` | How often to poll (default 12).                               |
| `label_width_mm` / `label_height_mm` / `gap_mm` | Label stock geometry.                     |
| `print_density` / `print_speed` | TSPL DENSITY / SPEED for the TTP-244 Pro.             |
| `dry_run`               | `true` prints the TSPL to the console instead of the printer. |

`config.json` is git-ignored (it holds the API secret).

## 4. Run

```bash
python label_print_agent.py
```

Leave it running (see step 6 to run it as a background service). To validate the
TSPL without a printer, set `"dry_run": true` and watch the console.

## 5. How a job flows

1. A Work Order for a label-enabled item reaches **In Process** → the app creates
   a `Label Print Request` with `Status = Pending` and
   `Number of Labels = Work Order Qty × Item's Labels Per Unit`.
2. The agent polls, finds the Pending row, renders `KIT_LABEL` TSPL, and RAW-prints
   `Number of Labels` copies.
3. On success it sets `Status = Printed` and stamps `Printed On`.
4. On failure it sets `Status = Failed` and writes the traceback to `Error Log`.
   Fix the issue, then use **Reprint Labels** on the request (resets it to Pending).

## 6. Run at startup (optional)

Simplest: **Task Scheduler** → *Create Task* → *Run whether user is logged on or
not* → *Trigger: At startup* → *Action:* `python C:\path\to\label_print_agent.py`
with *Start in* set to this folder. For a hardened service, wrap it with
[NSSM](https://nssm.cc/).

## 7. The KIT_LABEL template

Rendered in `label_print_agent.py` → `render_kit_label()`. It prints:

- Company name
- Product name
- `WO: <work order number>`
- Batch line (only if the request carries one)
- A QR code encoding the Work Order number

To add a new label type, add a `render_<name>()` function and register it in the
`TEMPLATES` dict, then set that template name on the Item (`Label Template`) — no
change to the Work Order workflow is needed.

### Note on the QR code

`QRCODE` is a TSPL2 command. The TTP-244 Pro firmware generally supports it; if
your unit renders nothing for the QR, either update firmware or swap the `QRCODE`
line for a 1-D `BARCODE` line in `render_kit_label()`. Run with `dry_run` first to
confirm the emitted TSPL, then test on-printer.
