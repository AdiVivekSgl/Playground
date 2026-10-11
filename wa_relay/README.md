# WA-AKG → Frappe relay (Phase 2)

Connects real WhatsApp messages to the Frappe endpoint from Phase 1
(`playground.api.whatsapp.handle_message`, see
`playground/playground/whatsapp/README.md`).

```
WhatsApp → WA-AKG ──webhook (Docker network)──→ wa-relay ──HTTPS + X-WA-AKG-SECRET──→ Frappe Cloud
WhatsApp ← WA-AKG ←──send API (Docker network)── wa-relay ←────────── {reply} ─────────────┘
```

Why a relay: WA-AKG webhooks are one-way notifications (WA-AKG ignores the
response), they use WA-AKG's own payload format and HMAC signature rather than
our `X-WA-AKG-SECRET` header, and Frappe Cloud can't reach WA-AKG on this PC to
send the reply. The relay bridges all three without changing WA-AKG or Frappe
and without exposing anything on this PC to the internet.

What it answers: 1:1 incoming **text** messages only. Group messages, your own
outgoing messages, media, stickers, status updates and receipts are ignored.
Retried webhooks are answered once.

## 1. Prerequisites

- WA-AKG running in Docker (container `wa-akg-app`, network `wa-akg_default` —
  check with `docker ps --format "{{.Names}}  {{.Networks}}"`).
- Phase 1 deployed and `wa_akg_secret` set on the Frappe Cloud site (your
  PowerShell `hello` test works).

## 2. Collect three secrets

| `.env` key | Where it comes from |
|---|---|
| `FRAPPE_WA_AKG_SECRET` | The same value you put in Frappe Cloud site config as `wa_akg_secret`. |
| `WA_AKG_API_KEY` | WA-AKG dashboard → profile / **API Key** → Generate (or `POST /api/user/api-key` while logged in). |
| `WEBHOOK_SECRET` | New random value — generate it (below) and use it again in step 4. |

Generate the webhook secret in PowerShell:

```powershell
$b = New-Object byte[] 32; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b); -join ($b | ForEach-Object { $_.ToString("x2") })
```

## 3. Configure and start the relay

```powershell
cd wa_relay
copy .env.example .env
notepad .env
```

Fill in `FRAPPE_URL`, the three secrets, and — while testing — your own number in
`ALLOWED_NUMBERS` (e.g. `919812345678`) so nobody else messaging this WhatsApp
number gets bot replies. Then:

```powershell
docker compose up -d --build
docker logs wa-relay
```

You should see `wa-relay listening on :8787 -> https://YOUR-SITE.frappe.cloud (allowlist: 1 number(s))`.

## 4. Register the webhook in WA-AKG

Find your session id (the `sessionId` field):

```powershell
Invoke-RestMethod -Uri "http://localhost:3000/api/sessions" -Headers @{ "X-API-Key" = "YOUR_WA_AKG_API_KEY" } | Select-Object name, sessionId, status
```

Create the webhook (or do the same in the WA-AKG dashboard → Webhooks):

```powershell
Invoke-RestMethod -Method Post -Uri "http://localhost:3000/api/webhooks/YOUR_SESSION_ID" `
  -Headers @{ "X-API-Key" = "YOUR_WA_AKG_API_KEY" } -ContentType "application/json" `
  -Body '{"name": "Frappe relay", "url": "http://wa-relay:8787/webhook", "secret": "YOUR_WEBHOOK_SECRET", "events": ["message.received"]}'
```

- The URL is `http://wa-relay:8787/webhook` — the relay's name on the Docker
  network, as WA-AKG sees it. **Not** `localhost` (inside WA-AKG's container
  that means WA-AKG itself).
- Subscribe to `message.received` only.
- The `secret` must equal `WEBHOOK_SECRET` in `.env`.

## 5. Test

Send `hello` to the WhatsApp number from a phone in `ALLOWED_NUMBERS`. Within a
few seconds you should get **Hello from Frontec ERP 👋**, and:

- `docker logs wa-relay` shows `replied to ********5678 (24 chars)`;
- Frappe **WhatsApp Query Log** shows a *Processed* row.

`ping` → `pong`; anything else → the "not implemented yet" reply.

## 6. Troubleshooting

| Symptom | Check |
|---|---|
| No relay log line at all | Webhook not firing: WA-AKG → webhook **logs** (`GET /api/webhooks/{sessionId}/{id}/logs`) or the dashboard. Wrong URL (must be `http://wa-relay:8787/webhook`), wrong session, or relay not on `wa-akg_default` (`docker network inspect wa-akg_default`). |
| `rejected webhook with missing/invalid signature` | Webhook `secret` in WA-AKG ≠ `WEBHOOK_SECRET` in `.env`. |
| `ignored message from … (not in ALLOWED_NUMBERS)` | Add the number (international digits, no `+`). |
| `Frappe rejected message … HTTP 401 Unauthorized` | `FRAPPE_WA_AKG_SECRET` ≠ site config `wa_akg_secret`. The Frappe log row says which. |
| `Frappe rejected … HTTP None` | Relay can't reach Frappe (DNS/internet from Docker, wrong `FRAPPE_URL`). |
| `WA-AKG send … failed: HTTP 401` | `WA_AKG_API_KEY` wrong or revoked. |
| `WA-AKG send … failed: HTTP 503` | WhatsApp session not connected — reconnect in WA-AKG. |
| `skipped: sender has no phone-number JID (LID-only)` (with `LOG_LEVEL=DEBUG`) | WhatsApp sent a privacy ID instead of the number; the relay refuses to guess a phone from it. |

Changing `.env`: `docker compose up -d` (recreates the container). Stopping:
`docker compose down`.

## 7. Security notes

- The relay publishes only `127.0.0.1:8787` (health check from this PC); WA-AKG
  reaches it over the private Docker network. No port forwarding or tunnel.
- Every webhook must carry a valid HMAC signature, otherwise 401 — a missing
  `WEBHOOK_SECRET` stops the relay from starting.
- `FRAPPE_URL` must be HTTPS, because the Frappe secret travels in a header.
- Secrets live only in `.env` (git-ignored). Logs show the last 4 digits of the
  phone number, never secrets or message bodies.
- The relay forwards only `phone`, `message`, `session_id`. Who the sender is
  and what they may see is decided in Frappe, not here.

## 8. Tests

Standard library only, so they run in a throwaway container (no local Python needed):

```powershell
docker run --rm -v "${PWD}:/app:ro" -w /app python:3.12-slim python -m unittest -v test_relay
```
