# WA-AKG ↔ Frappe WhatsApp integration

```
WhatsApp → WA-AKG → wa_relay → POST handle_message → number→User mapping → intent router → ERPNext (as that user) → JSON reply → WA-AKG → WhatsApp
```

- Phase 1: endpoint, log, `hello` / `ping`.
- Phase 2: `wa_relay/` container between WA-AKG and Frappe.
- Phase 3: **WhatsApp User** mapping (number → ERPNext User) and the first
  business query, `stock XYZ-123`. Unmapped numbers still only get `hello` / `ping`.

| Piece | Where |
|---|---|
| Endpoint | `playground/api/whatsapp.py` → `handle_message` |
| Phone normalisation, sender mapping | `playground/playground/whatsapp/phone.py` (`normalize_phone`, `get_user_from_phone`) |
| Number → User mapping | DocType **WhatsApp User** |
| Intent router | `playground/playground/whatsapp/router.py` (`COMMANDS`, `parse_natural_language`) |
| Run-as-user helper | `playground/playground/whatsapp/user_context.py` (`as_user`) |
| `stock` command | `playground/playground/whatsapp/stock.py` |
| Request log | DocType **WhatsApp Query Log** |
| Tests | `playground/playground/whatsapp/tests/` |

## 1. Endpoint

```
POST https://YOUR-SITE.frappe.cloud/api/method/playground.api.whatsapp.handle_message
```

Only `POST` is accepted. Rate-limited to 120 requests/minute per source IP
(over that, Frappe returns HTTP 429).

## 2. Authentication

Every request must send the shared secret in a header:

```
X-WA-AKG-SECRET: <the value of wa_akg_secret in site config>
```

- Missing or wrong header → HTTP 401, `{"success": false, "error": "Unauthorized"}`.
- If `wa_akg_secret` is **not set** on the site, **every** request is rejected (fail closed).
- The comparison is constant-time; the secret is never written to WhatsApp Query Log
  or Error Log. The log only records *why* a request was rejected (missing / wrong / not configured).

The endpoint is `allow_guest` because WA-AKG has no Frappe login — the secret
header is the authentication. It does **not** use a Frappe API key/secret, so
WA-AKG holds no credentials that could call `/api/resource` or any other method.

## 3. Request

`Content-Type: application/json`

```json
{
  "phone": "919XXXXXXXXX",
  "message": "hello",
  "session_id": "frontec-test"
}
```

| Field | Required | Notes |
|---|---|---|
| `phone` | yes | Any common format: `919812345678`, `+91 98123 45678`, `09812345678`, `9812345678`, `919812345678@s.whatsapp.net`. Normalised to `919812345678`. Bare 10-digit numbers get the default country code (91; override with `wa_akg_default_country_code` in site config). |
| `message` | yes | Non-empty string, max 4096 characters. |
| `session_id` | no | WA-AKG session name; stored in the log for debugging. |

Unknown extra fields are ignored.

## 4. Response

Success (HTTP 200):

```json
{ "success": true, "reply": "Hello from Frontec ERP 👋" }
```

Error:

```json
{ "success": false, "error": "Unauthorized" }
```

| HTTP | `error` | When |
|---|---|---|
| 400 | `Missing required parameter: phone` / `…: message` | Field absent or blank |
| 400 | `Invalid parameter: …` | Wrong JSON type (e.g. object/list instead of string), message too long |
| 400 | `Invalid phone number` | Fewer than 8 or more than 15 digits after normalisation |
| 401 | `Unauthorized` | Secret missing/wrong, or not configured on the site |
| 429 | *(Frappe's own error body)* | Rate limit exceeded |
| 500 | `Internal error` | Handler crashed — details are in **Error Log**, never in the response |

A body that is not valid JSON at all is rejected by Frappe itself before it
reaches this handler, so it gets Frappe's standard error response, not the shape above.

Commands:

| Message | Needs mapping | Reply |
|---|---|---|
| `hello` / `Hello` / `Hello!` | no | `Hello from Frontec ERP 👋` |
| `ping` | no | `pong` |
| `stock XYZ-123` | yes | Stock per warehouse — see [Stock command](#9-stock-command) |
| anything else | — | `Frontec ERP is connected, but this command is not implemented yet.` |

Business errors (unmapped number, no permission, unknown item) are normal
HTTP 200 replies with a polite message, so the sender always gets an answer.

## 5. Example

bash / Git Bash:

```bash
curl -X POST \
  https://YOUR-SITE.frappe.cloud/api/method/playground.api.whatsapp.handle_message \
  -H "Content-Type: application/json" \
  -H "X-WA-AKG-SECRET: YOUR_SECRET" \
  -d '{"phone": "919XXXXXXXXX", "message": "hello", "session_id": "frontec-test"}'
```

Windows PowerShell:

```powershell
Invoke-RestMethod -Method Post `
  -Uri "https://YOUR-SITE.frappe.cloud/api/method/playground.api.whatsapp.handle_message" `
  -Headers @{ "X-WA-AKG-SECRET" = "YOUR_SECRET" } `
  -ContentType "application/json; charset=utf-8" `
  -Body '{"phone": "919XXXXXXXXX", "message": "hello", "session_id": "frontec-test"}'
```

Then open **WhatsApp Query Log** in the desk: there should be a row with
status *Processed*, intent *hello* and the reply. A wrong secret shows up as an
*Unauthorized* row.

## 6. Configuring the secret on Frappe Cloud

1. Generate a long random secret (do this once, keep it in your password manager):

   ```powershell
   $b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString("x2") })
   ```

   (or `openssl rand -hex 32`). Hex keeps it safe to paste into an HTTP header.
2. Frappe Cloud dashboard → **Sites** → your site → **Config** tab → **Add Config**
   (some dashboard versions call it *Site Config* / *Edit Config*).
3. Key: `wa_akg_secret`, Type: *String*, Value: the secret → **Save**. Frappe Cloud
   writes it into the site's `site_config.json`; no deploy or restart is needed.
4. Optional: key `wa_akg_default_country_code`, value `91` (only if your bare
   10-digit numbers are not Indian).
5. Put the same secret into WA-AKG's webhook configuration as the
   `X-WA-AKG-SECRET` header (Phase 2).

On a self-hosted bench the equivalent is:

```bash
bench --site YOUR-SITE set-config wa_akg_secret "THE_SECRET"
```

**Rotating:** change the value in Frappe Cloud config and in WA-AKG at the same
time; requests with the old secret fail with 401 immediately. Never commit the
secret to git or paste it into a DocType field.

## 7. Logging

**WhatsApp Query Log** (System Manager: read/report/export/delete; rows are
created only by the endpoint) records phone, mapped ERPNext user, session id,
source IP, incoming message, detected intent, reply, status
(`Received → Processed | Error`, or `Unauthorized`) and any error reason.
Long messages are clipped to 1000 characters. Rows older than 90 days are
pruned by **Log Settings** (change the retention there).

## 8. Linking a WhatsApp number to an ERPNext user

Business commands only work for numbers an administrator has linked to an
ERPNext **User**. Everyone else can still say `hello` / `ping`, and gets a polite
"your number is not linked" reply to anything that needs data.

1. Desk → search **WhatsApp User** → **New** (System Manager only).
2. **WhatsApp Number**: the sender's number in any format (`+91 98123 45678`,
   `09812345678`, …). It is saved normalised (`919812345678`) — the same form the
   endpoint looks up, so formatting differences don't matter. One row per number
   (unique); one user may have several numbers.
3. **ERPNext User**: the user whose roles and User Permissions should apply. It
   must be an enabled user; `Administrator` and `Guest` are rejected.
4. **Enabled** (default on): untick to cut a number off without deleting it.
   Disabling the ERPNext User has the same effect.

Changes take effect on the next message — no restart. Every change is tracked
(**Track Changes** is on), and each request's resolved user is recorded in
**WhatsApp Query Log → ERPNext User**.

**Why a separate mapping instead of `User.mobile_no`:** which number may act as
which user is an authorization decision, and `mobile_no` is contact data:

- Users can edit it on their own profile, so the admin would not control who
  can reach ERPNext over WhatsApp — and a typo or a shared office number would
  silently grant access.
- It isn't unique: two users with the same number make the lookup ambiguous.
- It's free-format, so matching needs normalising every row on every message.
- There's no opt-in: every user who happens to have a mobile number on file
  would immediately be able to query ERPNext from WhatsApp.

**WhatsApp User** is writable only by System Manager, unique per normalised
number, has an explicit on/off switch, and keeps an audit trail.

## 9. Stock command

```
stock XYZ-123
```

Also understood (whole message must match, one item code, no LLM):
`How many pcs of XYZ-123 are available?`, `how much stock of item XYZ-123 do we have`,
`stock of XYZ-123`, `What is the stock for XYZ-123?`. Accepted unit words:
pcs, pieces, units, nos, qty, quantity, stock.

Reply:

```
📦 *XYZ-123* - Widget Blue
In stock: *120.5* Nos

• Stores - FT: 100 (projected 80)
• WIP - FT: 20.5
```

- Exact item code (case-insensitive); the canonical code is shown. No fuzzy or
  item-name search.
- Per-warehouse **actual qty** (from **Bin**), largest first; projected qty is
  shown when it differs. Warehouses with zero actual and projected qty are skipped.
- At most 10 warehouses are listed, then `…and N more warehouses`; the
  **In stock** total covers all of them.
- Replies: not linked → ask admin to add a *WhatsApp User*; no Item/Bin read
  permission → "doesn't have permission to view stock"; item missing **or not
  visible to that user** → "Item *X* not found" (the two are deliberately
  indistinguishable); non-stock item → "is not a stock item".

## 10. Permission model

- WA-AKG can call exactly one method. There is no route from WhatsApp text to
  `/api/resource`, SQL, an arbitrary DocType or an arbitrary method: the router
  only dispatches to functions listed in `COMMANDS`, and the natural-language
  patterns can only produce one of those commands.
- The ERPNext user is resolved **server-side** from the normalised WhatsApp
  number via **WhatsApp User** (`get_user_from_phone`). Nothing in the message or
  request body can choose or claim a user.
- The endpoint itself runs as **Guest**. A business handler refuses when
  `sender.user` is None; otherwise it wraps its lookup in
  `user_context.as_user(sender.user)`, which `frappe.set_user`s to the mapped
  user and restores the original user in a `finally` (also when the lookup
  raises), so logging and session handling afterwards run as Guest again.
- Inside, reads go only through permission-checked APIs: `frappe.has_permission`
  (with `user=`) and `frappe.get_list` — so the user's roles, **User
  Permissions** (e.g. restricted to certain warehouses) and permission query
  conditions all apply, exactly as in the desk. No `ignore_permissions`, no
  `frappe.get_all` / `frappe.db.*` on business data, never as Administrator
  (`as_user` refuses Administrator and Guest outright).
- The only privileged reads are the mapping lookup itself (WhatsApp User + the
  User's enabled flag) and the log writes — server configuration, never
  returned to the sender.

For `stock` this means the mapped user needs **read** on Item, Bin and
Warehouse (ERPNext's Stock User / Stock Manager roles have it; Sales User and
Purchase User usually do too).

## 11. Adding a command

```python
# playground/playground/whatsapp/order.py
def get_order_status(args, sender):
	if not sender.user:
		return NOT_LINKED_REPLY
	with as_user(sender.user):
		...  # frappe.has_permission / frappe.get_list only

# playground/playground/whatsapp/router.py
COMMANDS = {
	...
	"order": get_order_status,  # "order SO-00045"
}
```

Add tests for the handler (refused when unmapped, runs as the user, session
restored on error, permission denied) and a router test for the new command.

## 12. Tests

On the bench (no WhatsApp/WA-AKG calls, no data read or written — Frappe is mocked):

```bash
bench --site YOUR-SITE run-tests --module playground.playground.whatsapp.tests.test_handle_message
bench --site YOUR-SITE run-tests --module playground.playground.whatsapp.tests.test_phone
bench --site YOUR-SITE run-tests --module playground.playground.whatsapp.tests.test_router
bench --site YOUR-SITE run-tests --module playground.playground.whatsapp.tests.test_stock
```
