# Cevanos `/v1` routes CeDesk calls (AI features, orders, Governance)

For the Cevanos side (md section 16). CeDesk calls these with the workspace's Cevanos API
key (`Authorization: Bearer cvn_sk_...`), from `src/cedesk/cevanos_client.py`. The fake
Cevanos in `tests/db/test_ai.py` and `tests/db/test_cevanos.py` answers exactly these
shapes. A different shape on the Cevanos side needs only the client and those fakes to
change.

## Rules for every route

- Success: `2xx` with `{"data": ...}`. Refusal: `{"error": {"code", "message"}}`.
- **403 means "the key lacks the scope this route needs"**: CeDesk then tells the agent
  which scope is missing (409 `cevanos_scope_missing`). Use 401 for a bad key.
- 408, 429 and 5xx mean "try again": CeDesk shows 503 for the AI help and retries the
  outbox work. Any other 4xx is final.
- The AI routes (summaries, rewrite, translate, drafts) are billed to the Cevanos
  workspace's AI credits. They change nothing else, so CeDesk sends no Idempotency-Key.
- Card numbers are removed before any text is sent. Nothing CeDesk receives from them
  is stored.
- CeDesk waits up to 45 s for the AI routes and 20 s for the others.

## Scopes

| Scope | New? | Routes |
|---|---|---|
| `conversations:read` | exists | `GET /v1/conversations/{id}` (and the existing `.../messages`) |
| `conversations:write` | exists | the existing take-over, reply, close; and the hold decision |
| `ai:assist` | **new** | summaries, rewrite, translate, drafts |
| `contacts:read` | **new** | contacts by email, a contact's orders and payments |
| `analytics:read` | **new** | the AI numbers |

`POST /cevanos/connection/check` in CeDesk lists the new scopes a key still lacks.

## AI help

### `POST /v1/summaries` (AI1, `ai:assist`)

```json
{"text": "Customer (Priya): Where is my parcel? Order 1042.\n\nInternal note (Neha): Courier says Tuesday\n\nAgent (Neha): Checking with the courier."}
```

Answer: `{"data": {"summary": "Priya's parcel is late; ..."}}`

The text is the ticket's conversation, oldest first, at most 60 messages and 24,000
characters. Each line starts with `Customer`, `Agent`, `AI` or `Internal note`, followed
by the name in brackets.

### `POST /v1/rewrite` (AI2, `ai:assist`)

```json
{"text": "thx 4 waiting", "tone": "friendly"}
```

`tone` is one of `professional`, `friendly`, `empathetic` or `concise`. Answer:
`{"data": {"text": "Thank you for waiting, Priya."}}`

### `POST /v1/translate` (AI3, `ai:assist`)

```json
{"text": "Mera order kahan hai?", "to": "en"}
```

`to` is a language code (`en`, `hi`, `pt-BR`). Answer:
`{"data": {"text": "Where is my order?", "from": "hi"}}`. `from` may be null.

### `POST /v1/drafts` (AI4, `ai:assist`)

```json
{"text": "Customer (Priya): ...\n\nAgent (Neha): ...", "agent_id": "agent-77"}
```

This is the `/v1` version of the Inbox's draft. `agent_id` is the Cevanos AI agent of the
chat the ticket came from; it is `null` for other tickets, so use the workspace's default
agent. The text has no internal notes. Answer: `{"data": {"text": "Hi Priya, ..."}}`. The
draft goes into the agent's reply box and is never sent by itself.

## The chat's contact (AI6, `conversations:read`)

### `GET /v1/conversations/{id}`

```json
{"data": {"id": "conv-1", "contact": {"id": "c-7", "name": "Priya Sharma", "email": "priya@example.org"}}}
```

Every field of `contact` may be null, and `contact` itself may be null when the chat is
anonymous. CeDesk replaces "Chat customer" with the contact's name and email, and also
uses this route to find a chat ticket's contact for orders.

## Orders and payments (AI5, `contacts:read`)

### `GET /v1/contacts?email=priya@example.org`

Answer: `{"data": [{"id": "c-7"}]}`, the contacts with that email. CeDesk uses at most 3.
Used for tickets that don't come from a Cevanos chat (email, portal, web form).

### `GET /v1/contacts/{id}/orders`

```json
{"data": {
  "orders": [{"id": "o-1", "number": "#1042", "created_at": "2026-11-10T09:00:00Z",
              "payment": "paid", "fulfillment": null, "total": "1299.00",
              "currency": "INR", "items": 2,
              "url": "https://acme.myshopify.com/admin/orders/1"}],
  "unavailable": [{"source": "woocommerce", "message": "timed out"}]
}}
```

These come from every store connected in Cevanos. `unavailable` lists any store that
couldn't be read just now; CeDesk shows "Not read from woocommerce just now." The newest
5 orders are shown.

### `GET /v1/contacts/{id}/payments`

```json
{"data": {
  "payments": [{"id": "ch_1", "amount": "1299.00", "currency": "INR",
                "status": "succeeded", "refunded": false,
                "created_at": "2026-11-10T09:01:00Z", "description": "Order #1042",
                "url": null}],
  "unavailable": []
}}
```

## AI numbers in Reports (AI7, `analytics:read`)

### `GET /v1/analytics/summary?from=2026-10-17T00:00:00+00:00&to=2026-11-16T00:00:00+00:00`

```json
{"data": {"conversations": 200, "handed_over": 30, "handover_rate": 0.15,
          "ai_resolved": 170, "ai_resolved_rate": 0.85, "ai_csat": 4.4,
          "ai_csat_responses": 51}}
```

The period is `[from, to)`. Rates are 0-1 and `ai_csat` is 1-5; each may be null when
there is nothing to count. Counts are whole numbers.

## Governance holds (X7)

### In: `conversation.escalated` webhook with a hold

When the AI hands a chat over because Governance holds an action, add this to the
event's `data`:

```json
"hold": {"id": "hold_42", "action": "refund", "amount": "499.00", "currency": "INR"}
```

- `id`: up to 100 characters from `A-Z a-z 0-9 _ . : -`.
- `action`: a lowercase word (`refund`, `cancel_order`).
- `amount` and `currency`: optional, but always together.

CeDesk makes it an approval request on the ticket, once per hold id. A malformed hold
is refused with a 422, so Cevanos sends it again after fixing it.

### Out: `POST /v1/governance/holds/{id}/decision` (`conversations:write`)

```json
{"decision": "approved", "note": "Within policy", "decided_by": "Neha"}
```

`decision` is `approved` or `rejected`, and `note` may be null. The request carries an
`Idempotency-Key`, and a retry with the same key must not act twice. Answer `2xx` once
Cevanos has the decision; it then acts (or not) under Governance. A final 4xx, such as an
expired hold, is shown on the ticket and not retried.
