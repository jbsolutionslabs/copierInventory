# AI Chat Assistant — Design Spec

Date: 2026-10-08
Status: Approved for planning

## Purpose

Add a conversational assistant to the web UI that can:

1. Answer natural-language questions against the live inventory database
   (e.g. "how many Canon 5850s are there with 55,000 meters or less").
2. Read customer watchlist data ("is Joey looking for this machine?").
3. Create and edit watchlist entries on request ("add this machine to
   Joey's watchlist").

It is an internal tool for the team running this site, not a public-facing
feature — the rest of the site stays unauthenticated as today, but the
chat endpoint is gated separately because it has write access to customer
PII and spends real Anthropic API budget per message.

## Non-goals (v1)

- No delete capability via chat — removing a watchlist entry stays a
  manual UI action.
- No persisted chat history — each page load starts a fresh conversation.
- No per-user accounts — one shared passphrase for the whole team.
- No rate limiting beyond the passphrase gate and conversation trimming.
- No UI context passing (e.g. "this machine" does not mean "the row I'm
  currently looking at" — the user describes what they mean in the
  message). A future enhancement could prefill the chat input from a
  table row, but it's out of scope here.

## Architecture

```
docs/index.html (floating chat widget)
        │  POST /api/chat  { password, messages: [...] }
        ▼
routes/chat.py
        │  1. verify password against CHAT_PASSWORD env var
        │  2. trim messages to last ~12
        │  3. client.beta.messages.tool_runner(...)
        │       model="claude-sonnet-5"
        │       tools: query_inventory, get_watchlist,
        │              add_watchlist_item, update_watchlist_item
        ▼
Anthropic API (Claude Sonnet 5)
        │  tool_use requests
        ▼
Tool implementations in routes/chat.py
        │  reuse existing logic:
        │    - query_inventory    -> InventoryRecord + _record_to_dict
        │                            + aggregator._find_matches filtering
        │    - get_watchlist      -> WatchlistItem + _orm_to_dict
        │    - add_watchlist_item -> same insert as POST /api/watchlist
        │    - update_watchlist_item -> same update as PUT /api/watchlist/{id}
        ▼
Postgres (Railway) / SQLite (local)
```

## Backend

### `routes/chat.py` (new)

`POST /api/chat`

Request body:
```json
{
  "password": "string",
  "messages": [
    {"role": "user", "content": "how many canon 5850s..."},
    {"role": "assistant", "content": "..."}
  ]
}
```

Response body:
```json
{
  "reply": "string — Claude's final text response",
  "messages": [ /* full updated message array, including tool turns collapsed
                   to role+content pairs, for the client to hold as history */ ]
}
```

Behavior:
1. If `password` doesn't match `CHAT_PASSWORD` env var (or the env var is
   unset), return `401 {"error": "invalid passphrase"}` before making any
   Anthropic API call.
2. Trim `messages` to the last 12 entries (keeps token growth bounded
   across a long session; the client still holds full history for display,
   but only sends the tail).
3. Call `client.beta.messages.tool_runner(...)` with:
   - `model="claude-sonnet-5"`
   - `max_tokens=2048`
   - `output_config={"effort": "medium"}`
   - `system=<system prompt, see below>`
   - `tools=[query_inventory, get_watchlist, add_watchlist_item, update_watchlist_item]`
   - `messages=<trimmed messages>`
4. Run `runner.until_done()`, extract the final text response.
5. Return `{"reply": ..., "messages": ...}`.

### Tool definitions

All four are plain Python functions decorated with `@beta_tool`, taking a
`db: Session` closed over from the request's `get_db()` dependency (not an
LLM-visible param).

**`query_inventory`**
```
brand: str | None
model_contains: str | None
max_meter: float | None
min_meter: float | None
max_price: float | None
color: str | None        # "YES" | "NO"
state: str | None
condition: str | None
```
Implementation: query all `InventoryRecord` rows, convert each via the
existing `_record_to_dict` (from `routes/inventory.py`), then filter using
the same logic as `aggregator._find_matches` (brand exact-match
case-insensitive, model substring, numeric comparisons for meter/price,
etc.) — extended with `min_meter` and `condition`, which `_find_matches`
doesn't currently support, as local filter additions inside the tool
function rather than modifying `_find_matches` itself (that function is
also used by watchlist-match logic and shouldn't change behavior there).
Returns `{"count": N, "records": [...up to 200...]}`.

**`get_watchlist`**
```
customer_name: str | None   # case-insensitive substring match
```
Queries `WatchlistItem`, optional name filter, returns
`{"items": [...via _orm_to_dict...]}`.

**`add_watchlist_item`**
```
cust: str
email: str | None
phone: str | None
brand: str | None
model: str | None
max_meter: float | None
max_price: float | None
color: str | None
state: str | None
finisher: str | None
fax: str | None
notes: str | None
```
Same insert as `POST /api/watchlist` (new UUID, `created_at=utcnow()`).
Returns the created item via `_orm_to_dict`.

**`update_watchlist_item`**
```
id: str                     # from a prior get_watchlist call
email, phone, brand, model, max_meter, max_price,
color, state, finisher, fax, notes: all optional, only provided
fields are changed
```
Same partial-update semantics as `PUT /api/watchlist/{item_id}`. 404s
(via tool error result) if the id doesn't exist.

### System prompt

Grounds Claude in context: this is an internal assistant for a copier
wholesaler inventory aggregation tool. Explicit rules:
- Always get numbers from tool results — never estimate or recall from
  training data.
- When asked to edit a customer's watchlist entry, call `get_watchlist`
  first to resolve name → id. If zero or more than one customer matches,
  ask the user to clarify instead of guessing.
- Never claim an action succeeded without the tool result confirming it.
- Keep responses concise — this is a chat widget, not a report.

### Dependencies / env vars

- `requirements.txt`: add `anthropic`.
- New env vars: `ANTHROPIC_API_KEY`, `CHAT_PASSWORD` — added to Railway
  service env and to `.env.local` for local runs.

## Frontend (`docs/index.html`)

- Floating `💬` button, fixed bottom-right, visible on both tabs (outside
  the `tab-search`/`tab-watch` containers so it persists across tab
  switches).
- Click opens a slide-up chat panel: scrollable message list (user
  messages right-aligned, Claude's left-aligned, plain text with `\n`→`<br>`,
  no markdown rendering needed for v1), text input + send button, and a
  loading indicator while awaiting a response.
- **Password gate**: on first open, if no passphrase is stored in
  `localStorage` (key `ic_chat_pw`), show an inline prompt. Once a
  `POST /api/chat` call succeeds, store it and stop prompting. A 401
  response clears the stored value and re-prompts (handles a wrong guess
  or a rotated passphrase).
- **History**: a plain JS array (`chatHistory`) scoped to the page
  session; not persisted, matching the "reset each page load" decision.
- Each send: push the user message into `chatHistory`, POST the trimmed
  array + password to `/api/chat`, append Claude's reply to both the
  array and the visible panel.

## Error handling

- Network/API failure (Anthropic down, timeout, 5xx) → show an inline
  "Something went wrong, try again" message in the panel; don't clear the
  stored passphrase or existing history.
- Wrong/missing passphrase → 401, frontend re-prompts as above.
- A tool call that raises (e.g. `update_watchlist_item` with an id that
  no longer exists) → return a `tool_result` with `is_error: true` and a
  short message; Claude relays that to the user rather than the backend
  hard-failing the whole request.

## Testing plan

1. Backend: run the FastAPI app locally against SQLite (`uvicorn main:app`),
   hit `POST /api/chat` directly with a test passphrase and seeded
   inventory/watchlist rows to verify each tool fires with correct
   arguments and `query_inventory`'s count matches a manual filter.
2. Frontend: headless Playwright against the static page (same approach
   used earlier in this session) to verify the widget opens, the password
   prompt appears/stores correctly, and a send round-trip renders Claude's
   reply.
3. Manual pass with the real `ANTHROPIC_API_KEY` plugged in, running the
   three examples from the original request verbatim: a meter/brand count
   query, an "is X looking for Y" question, and a watchlist add.

## Open items for implementation

- Exact wording of the system prompt will be iterated during
  implementation/testing, not fixed by this spec.
- `_find_matches` itself is not modified; `query_inventory`'s extra
  filters (`min_meter`, `condition`) are applied as additional local
  filtering inside the tool function.
