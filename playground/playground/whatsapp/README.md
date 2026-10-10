# WA-AKG ↔ Frappe WhatsApp integration (Phase 1)

```
WhatsApp → WA-AKG → POST handle_message → intent router → (ERPNext, later) → JSON reply → WA-AKG → WhatsApp
```

Phase 1 proves connectivity only: `hello` and `ping` are answered, everything
else gets a "not implemented yet" reply. No ERPNext business data is reachable.

| Piece | Where |
|---|---|
| Endpoint | `playground/api/whatsapp.py` → `handle_message` |
| Phone normalisation, sender mapping | `playground/playground/whatsapp/phone.py` (`normalize_phone`, `get_user_from_phone`) |
| Intent router | `playground/playground/whatsapp/router.py` (`COMMANDS`) |
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

Commands in Phase 1:

| Message | Reply |
|---|---|
| `hello` / `Hello` / `Hello!` | `Hello from Frontec ERP 👋` |
| `ping` | `pong` |
| anything else | `Frontec ERP is connected, but this command is not implemented yet.` |

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

## 8. Security model (applies to every later phase)

- WA-AKG can call exactly one method. There is no route from WhatsApp text to
  `/api/resource`, SQL, an arbitrary DocType or an arbitrary method: the router
  only dispatches to functions listed in `COMMANDS`.
- The ERPNext user is resolved **server-side** from the WhatsApp number
  (`get_user_from_phone`). Nothing in the message or request body can choose or
  claim a user. In Phase 1 the mapping is a placeholder that always returns
  `user=None`.
- Business handlers (Phase 3+) must refuse when `sender.user` is None and must
  read data with that user's permissions (e.g. `frappe.has_permission` /
  `frappe.get_list` run as that user) — never via `ignore_permissions` or as
  Administrator.

## 9. Adding a command (Phase 3 onward)

```python
# playground/playground/whatsapp/router.py
def get_stock(args, sender):
	if not sender.user:
		return "Your number is not linked to an ERPNext user."
	...  # query Bin with sender.user's permissions, return text

COMMANDS = {
	"hello": hello,
	"ping": ping,
	"stock": get_stock,  # "stock XYZ-123"
}
```

Add a router test for each new command. Natural-language queries ("How many
pcs of XYZ-123 are available?") will be handled by a separate step in front of
the router, which still only ever resolves to a registered command.

## 10. Tests

On the bench (no WhatsApp/WA-AKG calls, no data written — Frappe is mocked):

```bash
bench --site YOUR-SITE run-tests --module playground.playground.whatsapp.tests.test_handle_message
bench --site YOUR-SITE run-tests --module playground.playground.whatsapp.tests.test_phone
bench --site YOUR-SITE run-tests --module playground.playground.whatsapp.tests.test_router
```
