# AI Chat Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a password-gated floating chat widget backed by Claude Sonnet 5 tool use, able to answer natural-language questions against live inventory and to read/create/edit (not delete) customer watchlist entries.

**Architecture:** A new stateless `POST /api/chat` FastAPI route (`routes/chat.py`) wraps `client.beta.messages.tool_runner` with four tools that each reuse existing DB-access/business logic (`aggregator._find_matches`, `routes/inventory.py:_record_to_dict`, `routes/watchlist.py`'s `_orm_to_dict` and its existing add/update route functions called directly as plain Python functions). The frontend (`docs/index.html`) gets a floating `💬` button that opens a chat panel, gated by a shared passphrase stored in `localStorage` after first successful use.

**Tech Stack:** FastAPI, SQLAlchemy, `anthropic` Python SDK (`client.beta.messages.tool_runner` + `@beta_tool`), Claude Sonnet 5 (`claude-sonnet-5`), vanilla JS/CSS in the existing single-file `docs/index.html`.

**Spec:** `docs/superpowers/specs/2026-10-08-ai-chat-assistant-design.md`

## Global Constraints

- Model is `claude-sonnet-5` — not Opus — per the user's explicit cost/use-case choice in the spec. Do not substitute.
- No delete tool. Chat can view, add, and edit watchlist entries only.
- One shared passphrase (`CHAT_PASSWORD` env var) gates the whole chat feature — not per-user auth. Missing env var must fail closed (401), never fail open.
- No conversation persistence server-side or client-side beyond the page session (`localStorage` is used only for the passphrase, not chat history).
- Reuse existing logic rather than reimplementing it: `aggregator._find_matches` for inventory filtering, `_record_to_dict` / `_orm_to_dict` for serialization, and `routes/watchlist.py`'s `add_watchlist_item` / `update_watchlist_item` route functions (called directly, bypassing FastAPI's dependency injection) for writes.
- New dependency: `anthropic`, added to `requirements.txt`. New env vars: `ANTHROPIC_API_KEY`, `CHAT_PASSWORD`.
- Tests follow this repo's existing convention (see `tests/test_identity.py`): an in-memory SQLite `Base.metadata.create_all` engine fixture, a per-test rollback-isolated `db` session fixture, and calling route/helper functions directly as plain Python functions rather than through FastAPI's `TestClient` (avoids triggering the app's `lifespan` — `init_db()` / `start_scheduler()` — as a side effect of importing a test module).

## Review Focus

- A Claude response whose final turn has no text block (e.g. ends on a refusal or a dangling tool call) must not surface as a silently empty chat bubble — it needs a user-visible fallback message. *(Task 3)*
- `CHAT_PASSWORD` unset in the environment must reject every request with 401, not accidentally allow access or crash. *(Task 3)*
- A long-running chat session must not send unbounded conversation history to the API on every turn — history is trimmed server-side before every Anthropic call. *(Task 3)*
- Chat message content (from the user or from Claude) rendered into the DOM must be HTML-escaped, not injected via `innerHTML` raw — otherwise a message containing `<script>` or other markup is a stored/reflected XSS vector. *(Task 4)*
- `update_watchlist_item` called with a stale/nonexistent id (e.g. the customer's entry was deleted through the UI between Claude's `get_watchlist` call and its `update_watchlist_item` call) must return a clean error the model can relay to the user, not raise an unhandled exception that aborts the whole chat turn. *(Task 2 and Task 3)*

---

### Task 1: Inventory query tool logic

**Files:**
- Create: `routes/chat.py`
- Modify: `requirements.txt`
- Test: `tests/test_chat.py`

**Interfaces:**
- Consumes: `db.InventoryRecord`, `db.ScrapeRun`, `routes.inventory._record_to_dict(rec, run_started_at) -> dict`, `aggregator._find_matches(req: dict, inventory: list[dict]) -> list[dict]`.
- Produces: `_query_inventory(db: Session, brand: str | None = None, model_contains: str | None = None, max_meter: float | None = None, min_meter: float | None = None, max_price: float | None = None, color: str | None = None, state: str | None = None, condition: str | None = None) -> dict` returning `{"count": int, "records": list[dict]}`. Used by Task 3's tool wrapper.

- [ ] **Step 1: Add the `anthropic` dependency**

This machine's `python3` is a Homebrew-managed install (PEP 668 "externally managed environment") — the project's existing dependencies (`fastapi`, `httpx`, `pytest`, `uvicorn`) are already installed directly into its site-packages, which only works with the override flag, so match that:

```bash
python3 -m pip install --break-system-packages anthropic
```

(Railway's build (`pip install -r requirements.txt` in `railway.toml`) runs in a fresh container with no such restriction — this flag is only needed for local installs on this machine.)

Add a line to `requirements.txt` (after `python-dotenv`):

```
anthropic
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_chat.py`:

```python
# tests/test_chat.py — Tests for routes/chat.py (AI chat assistant)
#
# Uses SQLite in-memory for speed, same pattern as tests/test_identity.py.
# Route/helper functions are called directly as plain Python functions
# rather than through FastAPI's TestClient, so importing this module never
# triggers the app's lifespan (init_db / start_scheduler).

import json
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from db import Base, InventoryRecord, WatchlistItem


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def db(engine):
    """Provide a fresh session with rollback isolation per test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _make_inventory(db, **kwargs):
    defaults = dict(
        source="rci", brand="Canon", model="imageRUNNER 5850",
        condition="Used", state="CA", total_meter=50000, price=2000,
        is_color="NO",
    )
    defaults.update(kwargs)
    rec = InventoryRecord(**defaults)
    db.add(rec)
    db.flush()
    return rec


# =============================================================================
# _query_inventory
# =============================================================================

def test_query_inventory_filters_brand_model_max_meter(db):
    from routes.chat import _query_inventory

    _make_inventory(db, model="imageRUNNER 5850", total_meter=50000)
    _make_inventory(db, model="imageRUNNER 5850i", total_meter=60000)
    _make_inventory(db, brand="Ricoh", model="MP C3004", total_meter=30000)

    result = _query_inventory(db, brand="Canon", model_contains="5850", max_meter=55000)

    assert result["count"] == 1
    assert result["records"][0]["model"] == "imageRUNNER 5850"


def test_query_inventory_min_meter_excludes_low_units(db):
    from routes.chat import _query_inventory

    _make_inventory(db, model="imageRUNNER 5850", total_meter=50000)
    _make_inventory(db, model="imageRUNNER 5850", total_meter=20000)

    result = _query_inventory(db, brand="Canon", model_contains="5850", min_meter=25000)

    assert result["count"] == 1
    assert result["records"][0]["total"] == 50000


def test_query_inventory_condition_filter(db):
    from routes.chat import _query_inventory

    _make_inventory(db, condition="Used")
    _make_inventory(db, condition="New")

    result = _query_inventory(db, condition="New")

    assert result["count"] == 1
    assert result["records"][0]["condition"] == "New"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_chat.py -v`
Expected: collection error / `ImportError: cannot import name '_query_inventory' from 'routes.chat'` (the file doesn't exist yet).

- [ ] **Step 4: Create `routes/chat.py` with `_query_inventory`**

```python
# routes/chat.py — POST /api/chat, an AI assistant with read access to
# live inventory and read/write (no delete) access to the customer
# watchlist, backed by Claude Sonnet 5 tool use.

import json
import os

import anthropic
from anthropic import beta_tool
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aggregator import _find_matches
from db import InventoryRecord, ScrapeRun, WatchlistItem, get_db
from routes.inventory import _record_to_dict

router = APIRouter()


def _query_inventory(
    db: Session,
    brand: str | None = None,
    model_contains: str | None = None,
    max_meter: float | None = None,
    min_meter: float | None = None,
    max_price: float | None = None,
    color: str | None = None,
    state: str | None = None,
    condition: str | None = None,
) -> dict:
    """Filter live inventory. Reuses aggregator._find_matches for the
    fields it already supports; min_meter and condition (which
    _find_matches doesn't support, since watchlist matching never needed
    them) are applied as additional local filtering."""
    last_run = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status == "success")
        .order_by(ScrapeRun.id.desc())
        .first()
    )
    run_started_at = last_run.started_at if last_run else None
    all_records = db.query(InventoryRecord).all()
    inventory_json = [_record_to_dict(r, run_started_at) for r in all_records]

    req = {
        "brand": brand or "",
        "model": model_contains or "",
        "color": color or "",
        "state": state or "",
        "maxMeter": max_meter,
        "maxPrice": max_price,
    }
    matches = _find_matches(req, inventory_json)

    if min_meter is not None:
        matches = [m for m in matches if (m.get("total") or 0) >= min_meter]
    if condition:
        matches = [
            m for m in matches
            if (m.get("condition") or "").lower() == condition.strip().lower()
        ]

    return {"count": len(matches), "records": matches[:200]}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_chat.py -v`
Expected: all 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add routes/chat.py tests/test_chat.py requirements.txt
git commit -m "feat: add inventory query logic for AI chat assistant"
```

---

### Task 2: Watchlist read/write tool logic

**Files:**
- Modify: `routes/chat.py`
- Modify: `tests/test_chat.py`

**Interfaces:**
- Consumes: `routes.watchlist.WatchlistItemIn`, `routes.watchlist._orm_to_dict(item) -> dict`, `routes.watchlist.add_watchlist_item(item: WatchlistItemIn, db: Session) -> dict`, `routes.watchlist.update_watchlist_item(item_id: str, item: WatchlistItemIn, db: Session) -> dict` (raises `HTTPException(404)` if not found).
- Produces:
  - `_get_watchlist(db: Session, customer_name: str | None = None) -> dict` returning `{"items": list[dict]}`.
  - `_add_watchlist(db: Session, cust: str, email=None, phone=None, brand=None, model=None, max_meter=None, max_price=None, color=None, state=None, finisher=None, fax=None, notes=None) -> dict` returning `{"ok": True, "item": dict}`.
  - `_update_watchlist(db: Session, id: str, email=None, phone=None, brand=None, model=None, max_meter=None, max_price=None, color=None, state=None, finisher=None, fax=None, notes=None) -> dict` returning `{"ok": True, "item": dict}`, raises `HTTPException(404)` on a missing id.
  All three are used by Task 3's tool wrapper.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat.py`:

```python
# =============================================================================
# _get_watchlist / _add_watchlist / _update_watchlist
# =============================================================================

def _make_watchlist_item(db, **kwargs):
    defaults = dict(
        id=str(uuid.uuid4()), name="Joey Smith", email="joey@example.com",
        brand="Canon", model="5850", created_at=datetime.utcnow(),
    )
    defaults.update(kwargs)
    item = WatchlistItem(**defaults)
    db.add(item)
    db.flush()
    return item


def test_get_watchlist_filters_by_name_substring(db):
    from routes.chat import _get_watchlist

    _make_watchlist_item(db, name="Joey Smith")
    _make_watchlist_item(db, name="Alice Jones")

    result = _get_watchlist(db, customer_name="joey")

    assert len(result["items"]) == 1
    assert result["items"][0]["cust"] == "Joey Smith"


def test_get_watchlist_no_filter_returns_all(db):
    from routes.chat import _get_watchlist

    _make_watchlist_item(db, name="Joey Smith")
    _make_watchlist_item(db, name="Alice Jones")

    result = _get_watchlist(db)

    assert len(result["items"]) == 2


def test_add_watchlist_creates_item(db):
    from routes.chat import _add_watchlist

    result = _add_watchlist(db, cust="New Customer", brand="Ricoh", max_meter=100000)

    assert result["ok"] is True
    assert result["item"]["cust"] == "New Customer"
    assert result["item"]["brand"] == "Ricoh"
    assert result["item"]["maxMeter"] == 100000

    stored = db.query(WatchlistItem).filter(WatchlistItem.name == "New Customer").first()
    assert stored is not None


def test_update_watchlist_changes_only_provided_fields(db):
    from routes.chat import _update_watchlist

    item = _make_watchlist_item(db, name="Joey Smith", brand="Canon", notes="original notes")

    result = _update_watchlist(db, id=item.id, notes="updated notes")

    assert result["item"]["notes"] == "updated notes"
    assert result["item"]["brand"] == "Canon"  # unchanged


def test_update_watchlist_raises_on_missing_id(db):
    from routes.chat import _update_watchlist

    with pytest.raises(HTTPException) as exc_info:
        _update_watchlist(db, id="nonexistent-id", notes="x")

    assert exc_info.value.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_chat.py -v`
Expected: the 6 new tests fail with `ImportError` (names don't exist in `routes.chat` yet); the 3 Task 1 tests still pass.

- [ ] **Step 3: Add the watchlist helpers to `routes/chat.py`**

Add to the imports at the top of `routes/chat.py`:

```python
from routes.watchlist import (
    WatchlistItemIn,
    _orm_to_dict,
    add_watchlist_item as _wl_add_route,
    update_watchlist_item as _wl_update_route,
)
```

Add below `_query_inventory`:

```python
def _get_watchlist(db: Session, customer_name: str | None = None) -> dict:
    items = db.query(WatchlistItem).order_by(WatchlistItem.created_at).all()
    if customer_name:
        needle = customer_name.strip().lower()
        items = [i for i in items if needle in (i.name or "").lower()]
    return {"items": [_orm_to_dict(i) for i in items]}


def _add_watchlist(
    db: Session,
    cust: str,
    email: str | None = None,
    phone: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    max_meter: float | None = None,
    max_price: float | None = None,
    color: str | None = None,
    state: str | None = None,
    finisher: str | None = None,
    fax: str | None = None,
    notes: str | None = None,
) -> dict:
    item = WatchlistItemIn(
        cust=cust, email=email, phone=phone, brand=brand, model=model,
        maxMeter=max_meter, maxPrice=max_price, color=color, state=state,
        finisher=finisher, fax=fax, notes=notes,
    )
    return _wl_add_route(item, db)


def _update_watchlist(
    db: Session,
    id: str,
    email: str | None = None,
    phone: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    max_meter: float | None = None,
    max_price: float | None = None,
    color: str | None = None,
    state: str | None = None,
    finisher: str | None = None,
    fax: str | None = None,
    notes: str | None = None,
) -> dict:
    item = WatchlistItemIn(
        id=id, email=email, phone=phone, brand=brand, model=model,
        maxMeter=max_meter, maxPrice=max_price, color=color, state=state,
        finisher=finisher, fax=fax, notes=notes,
    )
    return _wl_update_route(id, item, db)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_chat.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add routes/chat.py tests/test_chat.py
git commit -m "feat: add watchlist read/write logic for AI chat assistant"
```

---

### Task 3: `/api/chat` endpoint — tool wiring, password gate, and env setup

**Files:**
- Modify: `routes/chat.py`
- Modify: `main.py`
- Modify: `.env` (local only — gitignored, not committed)
- Modify: `tests/test_chat.py`

**Interfaces:**
- Consumes: `_query_inventory`, `_get_watchlist`, `_add_watchlist`, `_update_watchlist` (Tasks 1-2); `anthropic.beta_tool`; `client.beta.messages.tool_runner(model, max_tokens, system, output_config, tools, messages)`.
- Produces: `ChatMessage(role: str, content: str)`, `ChatRequest(password: str, messages: list[ChatMessage])`, `ChatResponse(reply: str)` (Pydantic models), `chat(req: ChatRequest, db: Session) -> ChatResponse` (raises `HTTPException(401)` on bad/missing password), `_make_tools(db: Session) -> list` (the four `@beta_tool`-wrapped closures, each with a `.name` attribute matching its function name — consumed by Task 4's manual verification only, not by other tasks).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat.py`:

```python
# =============================================================================
# /api/chat endpoint
# =============================================================================

def test_chat_rejects_wrong_password(monkeypatch, db):
    from routes.chat import ChatMessage, ChatRequest, chat

    monkeypatch.setenv("CHAT_PASSWORD", "correct-horse")

    req = ChatRequest(password="wrong", messages=[ChatMessage(role="user", content="hi")])

    with pytest.raises(HTTPException) as exc_info:
        chat(req, db)

    assert exc_info.value.status_code == 401


def test_chat_rejects_missing_password_env(monkeypatch, db):
    from routes.chat import ChatMessage, ChatRequest, chat

    monkeypatch.delenv("CHAT_PASSWORD", raising=False)

    req = ChatRequest(password="anything", messages=[ChatMessage(role="user", content="hi")])

    with pytest.raises(HTTPException) as exc_info:
        chat(req, db)

    assert exc_info.value.status_code == 401


def test_chat_empty_messages_skips_anthropic_call(monkeypatch, db):
    from routes.chat import ChatRequest, chat

    monkeypatch.setenv("CHAT_PASSWORD", "correct-horse")

    with patch("routes.chat.anthropic.Anthropic") as mock_anthropic_cls:
        req = ChatRequest(password="correct-horse", messages=[])
        result = chat(req, db)

    assert result.reply == ""
    mock_anthropic_cls.assert_not_called()


def test_chat_trims_history_and_returns_reply(monkeypatch, db):
    from routes.chat import ChatMessage, ChatRequest, chat

    monkeypatch.setenv("CHAT_PASSWORD", "correct-horse")

    fake_message = SimpleNamespace(content=[SimpleNamespace(type="text", text="mocked reply")])
    mock_client = MagicMock()
    mock_client.beta.messages.tool_runner.return_value = [fake_message]

    with patch("routes.chat.anthropic.Anthropic", return_value=mock_client):
        long_history = [ChatMessage(role="user", content=f"message {i}") for i in range(20)]
        req = ChatRequest(password="correct-horse", messages=long_history)
        result = chat(req, db)

    assert result.reply == "mocked reply"

    sent_messages = mock_client.beta.messages.tool_runner.call_args.kwargs["messages"]
    assert len(sent_messages) == 12
    assert sent_messages[-1]["content"] == "message 19"


def test_chat_falls_back_when_no_text_block(monkeypatch, db):
    from routes.chat import ChatMessage, ChatRequest, chat

    monkeypatch.setenv("CHAT_PASSWORD", "correct-horse")

    fake_message = SimpleNamespace(content=[SimpleNamespace(type="tool_use", text=None)])
    mock_client = MagicMock()
    mock_client.beta.messages.tool_runner.return_value = [fake_message]

    with patch("routes.chat.anthropic.Anthropic", return_value=mock_client):
        req = ChatRequest(password="correct-horse", messages=[ChatMessage(role="user", content="hi")])
        result = chat(req, db)

    assert result.reply == "I wasn't able to generate a response to that — try rephrasing your question."


def test_update_watchlist_tool_wrapper_returns_error_json_on_missing_id(db):
    from routes.chat import _make_tools

    tools = _make_tools(db)
    update_tool = next(t for t in tools if t.name == "update_watchlist_item")

    result_json = update_tool(id="nonexistent-id", notes="x")
    result = json.loads(result_json)

    assert "error" in result
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/test_chat.py -v`
Expected: the 6 new tests fail with `ImportError` (`ChatMessage`, `ChatRequest`, `chat`, `_make_tools` don't exist yet); the 9 existing tests still pass.

- [ ] **Step 3: Add the endpoint, tool wrapper, and system prompt to `routes/chat.py`**

Append to `routes/chat.py` (after the Task 2 helpers):

```python
MAX_HISTORY = 12

SYSTEM_PROMPT = (
    "You are an internal assistant for a copier wholesaler inventory "
    "aggregation tool used by a small sales team. You have tools to look "
    "up live inventory and to view, add, or edit customer \"watchlist\" "
    "requests (what specific customers are looking for).\n\n"
    "Rules:\n"
    "- Always get numbers and facts from tool results. Never estimate, "
    "guess, or recall counts from memory.\n"
    "- When asked to edit an existing customer's watchlist entry, call "
    "get_watchlist first to find their id. If zero or more than one "
    "customer matches the name given, ask the user to clarify which "
    "customer before making any change.\n"
    "- Never claim an action succeeded (added, updated) unless the tool "
    "result confirms it.\n"
    "- Keep responses concise and conversational -- this is a chat "
    "widget, not a report. Use plain text, no markdown tables.\n"
    "- You cannot delete watchlist entries. If asked to delete one, say "
    "that has to be done manually in the Customer Watchlist tab."
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    password: str
    messages: list[ChatMessage]


class ChatResponse(BaseModel):
    reply: str


def _make_tools(db: Session):
    @beta_tool
    def query_inventory(
        brand: str | None = None,
        model_contains: str | None = None,
        max_meter: float | None = None,
        min_meter: float | None = None,
        max_price: float | None = None,
        color: str | None = None,
        state: str | None = None,
        condition: str | None = None,
    ) -> str:
        """Search the live copier inventory.

        Args:
            brand: Exact brand name, e.g. "Canon". Case-insensitive.
            model_contains: Substring to match against the model name, e.g. "5850".
            max_meter: Maximum total meter count (inclusive).
            min_meter: Minimum total meter count (inclusive).
            max_price: Maximum price in dollars (inclusive).
            color: "YES" for color units only, "NO" for black & white only.
            state: Two-letter US state code the unit is located in.
            condition: Condition filter, e.g. "Used", "Refurbished", "New".
        """
        return json.dumps(_query_inventory(
            db, brand=brand, model_contains=model_contains, max_meter=max_meter,
            min_meter=min_meter, max_price=max_price, color=color, state=state,
            condition=condition,
        ))

    @beta_tool
    def get_watchlist(customer_name: str | None = None) -> str:
        """List customer watchlist requests, optionally filtered by customer name.

        Args:
            customer_name: Case-insensitive substring match against the customer's name. Omit to list everyone.
        """
        return json.dumps(_get_watchlist(db, customer_name=customer_name))

    @beta_tool
    def add_watchlist_item(
        cust: str,
        email: str | None = None,
        phone: str | None = None,
        brand: str | None = None,
        model: str | None = None,
        max_meter: float | None = None,
        max_price: float | None = None,
        color: str | None = None,
        state: str | None = None,
        finisher: str | None = None,
        fax: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Create a new customer watchlist request.

        Args:
            cust: Customer's name. Required.
            email: Customer's email address.
            phone: Customer's phone number.
            brand: Brand the customer wants, e.g. "Canon".
            model: Model substring the customer wants, e.g. "5850".
            max_meter: Maximum acceptable meter count.
            max_price: Maximum acceptable price in dollars.
            color: "YES" for color only, "NO" for black & white only, omit for either.
            state: Two-letter US state code preference.
            finisher: "yes" if a finisher is required, "no" if not required, omit for either.
            fax: "YES" if fax is required, "NO" if not required, omit for either.
            notes: Any other details about the request.
        """
        return json.dumps(_add_watchlist(
            db, cust=cust, email=email, phone=phone, brand=brand, model=model,
            max_meter=max_meter, max_price=max_price, color=color, state=state,
            finisher=finisher, fax=fax, notes=notes,
        ))

    @beta_tool
    def update_watchlist_item(
        id: str,
        email: str | None = None,
        phone: str | None = None,
        brand: str | None = None,
        model: str | None = None,
        max_meter: float | None = None,
        max_price: float | None = None,
        color: str | None = None,
        state: str | None = None,
        finisher: str | None = None,
        fax: str | None = None,
        notes: str | None = None,
    ) -> str:
        """Update fields on an existing customer watchlist request. Only provided fields are changed.

        Args:
            id: The watchlist item's id, from a prior get_watchlist call. Required.
            email: New email address.
            phone: New phone number.
            brand: New brand preference.
            model: New model substring.
            max_meter: New maximum meter count.
            max_price: New maximum price.
            color: "YES", "NO", or omit for either.
            state: New two-letter state code.
            finisher: "yes", "no", or omit for either.
            fax: "YES", "NO", or omit for either.
            notes: New notes text.
        """
        try:
            result = _update_watchlist(
                db, id=id, email=email, phone=phone, brand=brand, model=model,
                max_meter=max_meter, max_price=max_price, color=color, state=state,
                finisher=finisher, fax=fax, notes=notes,
            )
        except HTTPException as e:
            return json.dumps({"error": str(e.detail)})
        return json.dumps(result)

    return [query_inventory, get_watchlist, add_watchlist_item, update_watchlist_item]


@router.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)) -> ChatResponse:
    expected_password = os.environ.get("CHAT_PASSWORD")
    if not expected_password or req.password != expected_password:
        raise HTTPException(status_code=401, detail="invalid passphrase")

    history = [{"role": m.role, "content": m.content} for m in req.messages][-MAX_HISTORY:]
    if not history:
        # Used by the frontend to validate the passphrase without spending
        # an Anthropic API call.
        return ChatResponse(reply="")

    tools = _make_tools(db)

    try:
        client = anthropic.Anthropic()
        runner = client.beta.messages.tool_runner(
            model="claude-sonnet-5",
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            output_config={"effort": "medium"},
            tools=tools,
            messages=history,
        )
        last_message = None
        for message in runner:
            last_message = message
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=500, detail="server misconfigured: invalid Anthropic API key")
    except anthropic.APIStatusError as e:
        raise HTTPException(status_code=502, detail=f"Claude API error: {e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=502, detail="could not reach Claude API")

    if last_message is None:
        raise HTTPException(status_code=502, detail="no response from Claude")

    reply = next((b.text for b in last_message.content if b.type == "text"), "")
    if not reply:
        reply = "I wasn't able to generate a response to that — try rephrasing your question."

    return ChatResponse(reply=reply)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/test_chat.py -v`
Expected: all 15 tests PASS.

- [ ] **Step 5: Wire environment loading and mount the router**

Modify `main.py` — add `from dotenv import load_dotenv` to the imports and call it immediately after, before `ALLOWED_ORIGINS` is read:

```python
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from db import init_db
from scheduler import start_scheduler, stop_scheduler

load_dotenv()

ALLOWED_ORIGINS = os.environ.get(
```

(leave the rest of that block unchanged). Then mount the new router alongside the others:

```python
from routes.chat      import router as chat_router
from routes.inventory import router as inventory_router
from routes.scrape    import router as scrape_router
from routes.uploads   import router as uploads_router
from routes.watchlist import router as watchlist_router

app.include_router(inventory_router)
app.include_router(uploads_router)
app.include_router(watchlist_router)
app.include_router(scrape_router)
app.include_router(chat_router)
```

`load_dotenv()` reads `.env` in the working directory and only sets variables that aren't already present in the environment — it's a no-op in production on Railway, where env vars are injected directly by the platform, and only affects local `uvicorn` runs.

- [ ] **Step 6: Add local env var placeholders**

Add two lines to `.env` (already gitignored — this file is never committed):

```
ANTHROPIC_API_KEY=
CHAT_PASSWORD=
```

Leave the values empty here. **Do not paste the real Anthropic API key into chat with an assistant or into any file that gets committed** — fill in `ANTHROPIC_API_KEY` and `CHAT_PASSWORD` directly in `.env` yourself, and set the same two variables in the Railway service's environment settings for production.

- [ ] **Step 7: Commit**

```bash
git add routes/chat.py tests/test_chat.py main.py requirements.txt
git commit -m "feat: wire up POST /api/chat endpoint with Claude Sonnet 5 tool use"
```

(`.env` is gitignored and won't be picked up by `git add` of tracked paths — no separate step needed, and don't force-add it.)

---

### Task 4: Floating chat widget (frontend)

**Files:**
- Modify: `docs/index.html`

**Interfaces:**
- Consumes: `POST {API_BASE}/api/chat` with body `{password: string, messages: [{role, content}]}`, response `{reply: string}` or HTTP 401 on bad passphrase (Task 3).
- Produces: no new interfaces consumed elsewhere — this is the UI leaf.

- [ ] **Step 1: Add CSS**

In `docs/index.html`, add before the closing `</style>` tag (after the existing `#upload-status{...}` / email-modal rules added earlier):

```css
/* ── AI chat widget ── */
#chat-fab{position:fixed;bottom:20px;right:20px;width:54px;height:54px;border-radius:50%;background:var(--navy);color:#fff;border:none;font-size:22px;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,.25);z-index:200;display:flex;align-items:center;justify-content:center}
#chat-fab:hover{background:var(--navy2)}
#chat-panel{position:fixed;bottom:86px;right:20px;width:360px;max-width:92vw;height:480px;max-height:70vh;background:#fff;border-radius:10px;box-shadow:0 8px 40px rgba(0,0,0,.3);display:none;flex-direction:column;z-index:200;overflow:hidden}
#chat-panel.open{display:flex}
#chat-panel-hdr{background:var(--navy);color:#fff;padding:10px 14px;font-weight:800;font-size:13px;display:flex;justify-content:space-between;align-items:center;flex-shrink:0}
#chat-panel-hdr .close{cursor:pointer;font-size:16px;opacity:.8}
#chat-panel-hdr .close:hover{opacity:1}
#chat-messages{flex:1;overflow-y:auto;padding:12px;font-size:12px}
.chat-msg{margin-bottom:10px;max-width:85%;padding:8px 11px;border-radius:10px;line-height:1.4;white-space:pre-wrap;word-wrap:break-word}
.chat-msg.user{background:var(--navy);color:#fff;margin-left:auto;border-bottom-right-radius:2px}
.chat-msg.assistant{background:#F0F3F9;color:var(--text);margin-right:auto;border-bottom-left-radius:2px}
#chat-input-row{display:flex;gap:6px;padding:10px;border-top:1px solid var(--bdr);flex-shrink:0}
#chat-input{flex:1;border:1.5px solid var(--bdr);border-radius:6px;padding:7px 9px;font-size:12px;font-family:inherit;resize:none}
#chat-pw-row{padding:14px;font-size:12px}
#chat-pw-row input{width:100%;border:1.5px solid var(--bdr);border-radius:6px;padding:7px 9px;font-size:12px;margin:8px 0;box-sizing:border-box}
```

- [ ] **Step 2: Add HTML**

Add just before the closing `</body>` (or immediately before the final `<script>` block if `</body>` isn't present as a standalone tag — place it as a sibling of the existing `<div class="modal-bg" id="email-modal">` block, outside any tab container so it persists across tab switches):

```html
<!-- ── AI chat widget ─────────────────────────────────────────────────── -->
<button id="chat-fab" onclick="toggleChatPanel()">💬</button>
<div id="chat-panel">
  <div id="chat-panel-hdr">
    <span>✨ Ask AI</span>
    <span class="close" onclick="toggleChatPanel()">✕</span>
  </div>
  <div id="chat-pw-row" style="display:none">
    <div>Enter the team passphrase to use the assistant:</div>
    <input type="password" id="chat-pw-input" placeholder="Passphrase" onkeydown="if(event.key==='Enter')submitChatPassword()">
    <button class="btn bs" style="width:100%" onclick="submitChatPassword()">Unlock</button>
    <div id="chat-pw-error" style="color:#922B21;font-size:11px;margin-top:6px"></div>
  </div>
  <div id="chat-messages" style="display:none"></div>
  <div id="chat-input-row" style="display:none">
    <textarea id="chat-input" rows="1" placeholder="Ask about inventory or a customer..." onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();sendChatMessage();}"></textarea>
    <button class="btn bs" onclick="sendChatMessage()">➤</button>
  </div>
</div>
```

- [ ] **Step 3: Add JS**

Add inside the existing `<script>` block (anywhere after `API_BASE` is defined — e.g. right after the `emailCustomer`/`closeEmailModal` functions added earlier):

```javascript
/* ════════════════════════════════════════════════════════════════
   AI CHAT WIDGET
   ════════════════════════════════════════════════════════════════ */
let chatHistory = [];
let chatPassword = null;

function toggleChatPanel() {
  const panel = document.getElementById('chat-panel');
  panel.classList.toggle('open');
  if (!panel.classList.contains('open')) return;

  chatPassword = localStorage.getItem('ic_chat_pw');
  const unlocked = !!chatPassword;
  document.getElementById('chat-pw-row').style.display    = unlocked ? 'none'  : 'block';
  document.getElementById('chat-messages').style.display  = unlocked ? 'block' : 'none';
  document.getElementById('chat-input-row').style.display = unlocked ? 'flex'  : 'none';
  document.getElementById(unlocked ? 'chat-input' : 'chat-pw-input').focus();
}

async function submitChatPassword() {
  const pw = document.getElementById('chat-pw-input').value.trim();
  if (!pw) return;
  const errEl = document.getElementById('chat-pw-error');
  errEl.textContent = 'Checking...';
  try {
    const res = await fetch(API_BASE + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: pw, messages: [] }),
    });
    if (res.status === 401) {
      errEl.textContent = 'Incorrect passphrase.';
      return;
    }
    if (!res.ok) {
      errEl.textContent = 'Could not reach the assistant. Try again shortly.';
      return;
    }
    chatPassword = pw;
    localStorage.setItem('ic_chat_pw', pw);
    chatHistory = [];
    document.getElementById('chat-pw-row').style.display    = 'none';
    document.getElementById('chat-messages').style.display  = 'block';
    document.getElementById('chat-input-row').style.display = 'flex';
    renderChatMessages();
    document.getElementById('chat-input').focus();
  } catch (e) {
    errEl.textContent = 'Could not reach the assistant. Try again shortly.';
  }
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

function renderChatMessages() {
  const el = document.getElementById('chat-messages');
  el.innerHTML = chatHistory.map(m =>
    `<div class="chat-msg ${m.role}">${escapeHtml(m.content)}</div>`
  ).join('');
  el.scrollTop = el.scrollHeight;
}

async function sendChatMessage() {
  const input = document.getElementById('chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  chatHistory.push({ role: 'user', content: text });
  renderChatMessages();

  const el = document.getElementById('chat-messages');
  const loadingDiv = document.createElement('div');
  loadingDiv.className = 'chat-msg assistant';
  loadingDiv.id = 'chat-loading';
  loadingDiv.textContent = '...';
  el.appendChild(loadingDiv);
  el.scrollTop = el.scrollHeight;

  try {
    const res = await fetch(API_BASE + '/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password: chatPassword, messages: chatHistory }),
    });
    document.getElementById('chat-loading')?.remove();

    if (res.status === 401) {
      localStorage.removeItem('ic_chat_pw');
      chatPassword = null;
      chatHistory.push({ role: 'assistant', content: 'Your passphrase is no longer valid. Please reopen the chat and re-enter it.' });
      renderChatMessages();
      return;
    }
    if (!res.ok) {
      chatHistory.push({ role: 'assistant', content: 'Something went wrong — please try again.' });
      renderChatMessages();
      return;
    }
    const data = await res.json();
    chatHistory.push({ role: 'assistant', content: data.reply });
    renderChatMessages();
  } catch (e) {
    document.getElementById('chat-loading')?.remove();
    chatHistory.push({ role: 'assistant', content: 'Something went wrong — please try again.' });
    renderChatMessages();
  }
}
```

- [ ] **Step 4: Verify with a headless browser script**

This repo has no JS test framework — verify the same way earlier work in this session was verified: a throwaway Playwright script against the static page with `fetch` stubbed, run once during implementation (not committed).

```bash
mkdir -p /tmp/claude-501/chat-widget-check && cd /tmp/claude-501/chat-widget-check
npm init -y >/dev/null 2>&1 && npm install playwright@1.60.0 >/dev/null 2>&1
cd /Users/josephbongar/Desktop/copierInventory
python3 -m http.server 8731 --directory docs >/tmp/claude-501/httpserver.log 2>&1 &
sleep 1
```

Write `/tmp/claude-501/chat-widget-check/test.js`:

```javascript
const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  page.on('pageerror', e => console.log('PAGEERROR:', e.message));

  await page.goto('http://localhost:8731/index.html');

  // Stub fetch: wrong password -> 401, correct password -> 200 empty reply,
  // any message containing "xss" -> echoes a literal <script> payload back,
  // to verify it renders as text and never executes.
  await page.evaluate(() => {
    window.fetch = async (url, opts) => {
      const body = JSON.parse(opts.body);
      if (body.password !== 'right-pw') {
        return { status: 401, ok: false, json: async () => ({}) };
      }
      if (body.messages.length === 0) {
        return { status: 200, ok: true, json: async () => ({ reply: '' }) };
      }
      const lastMsg = body.messages[body.messages.length - 1].content;
      if (lastMsg.includes('xss')) {
        return { status: 200, ok: true, json: async () => ({ reply: '<script>window.__xssRan=true</script>' }) };
      }
      return { status: 200, ok: true, json: async () => ({ reply: 'mocked assistant reply' }) };
    };
  });

  await page.click('#chat-fab');
  await page.fill('#chat-pw-input', 'wrong-pw');
  await page.click('#chat-pw-row button');
  const errorText = await page.textContent('#chat-pw-error');
  console.log('wrong password error:', errorText);

  await page.fill('#chat-pw-input', 'right-pw');
  await page.click('#chat-pw-row button');
  const inputVisible = await page.isVisible('#chat-input-row');
  console.log('input visible after correct password:', inputVisible);

  const storedPw = await page.evaluate(() => localStorage.getItem('ic_chat_pw'));
  console.log('stored password:', storedPw);

  await page.fill('#chat-input', 'hello there');
  await page.click('#chat-input-row button');
  await page.waitForSelector('.chat-msg.assistant:has-text("mocked assistant reply")');
  console.log('got assistant reply: yes');

  await page.fill('#chat-input', 'try some xss');
  await page.click('#chat-input-row button');
  await page.waitForTimeout(300);
  const xssRan = await page.evaluate(() => window.__xssRan === true);
  const lastBubbleText = await page.evaluate(() => {
    const bubbles = document.querySelectorAll('.chat-msg.assistant');
    return bubbles[bubbles.length - 1].textContent;
  });
  console.log('xss executed (must be false):', xssRan);
  console.log('last bubble rendered as text:', lastBubbleText);

  await page.screenshot({ path: '/tmp/claude-501/chat-widget-check/panel.png' });

  await browser.close();
})();
```

```bash
cd /tmp/claude-501/chat-widget-check && node test.js
```

Expected output: `wrong password error: Incorrect passphrase.`, `input visible after correct password: true`, `stored password: right-pw`, `got assistant reply: yes`, `xss executed (must be false): false`, and `last bubble rendered as text: <script>window.__xssRan=true</script>` (the literal text, not executed markup).

Clean up:

```bash
lsof -ti:8731 -sTCP:LISTEN | xargs -r kill
rm -rf /tmp/claude-501/chat-widget-check /tmp/claude-501/httpserver.log
```

- [ ] **Step 5: Commit**

```bash
git add docs/index.html
git commit -m "feat: add floating AI chat widget to the frontend"
```

---

### Task 5: Docs and manual end-to-end verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing consumed elsewhere — documentation and final verification only.

- [ ] **Step 1: Document the new env vars**

In `README.md`, find the `### Railway Env Vars` line:

```
DATABASE_URL (auto), UPLOAD_DIR=/data/uploads, RESEND_API_KEY, EMAIL_FROM, ALLOWED_ORIGINS
```

Replace it with:

```
DATABASE_URL (auto), UPLOAD_DIR=/data/uploads, RESEND_API_KEY, EMAIL_FROM, ALLOWED_ORIGINS, ANTHROPIC_API_KEY, CHAT_PASSWORD
```

- [ ] **Step 2: Manual end-to-end verification with the real API key**

This step needs the real `ANTHROPIC_API_KEY` and a `CHAT_PASSWORD` of your choosing, set directly in `.env` (Task 3, Step 6) — do not paste the key into chat.

```bash
cd /Users/josephbongar/Desktop/copierInventory
uvicorn main:app --reload --port 8000 &
sleep 2
python3 -m http.server 8731 --directory docs &
```

Open `http://localhost:8731/index.html` in a real browser (point `API_BASE` in `docs/index.html` at `http://localhost:8000` temporarily if it isn't already, then revert before committing — or just test directly against the deployed Railway `API_BASE` once this branch is deployed there with the env vars set). Click the `💬` button, enter the passphrase, and run the three examples from the original request verbatim:

1. "How many Canon 5850s are there with 55,000 meters or less?" — verify the count matches what Inventory Search returns for the same filter.
2. "Is Joey looking for this machine?" followed by a real brand/model from inventory, e.g. "Is Joey looking for a Canon imageRUNNER 5850?" — verify it correctly reports a match or no match against an actual watchlist entry (add one for a test customer first if none exists).
3. "Add a Canon imageRUNNER 5850 under 60,000 meters to Joey's watchlist" — verify a new entry appears in the Customer Watchlist tab afterward.

Stop both local servers when done:

```bash
lsof -ti:8000 -sTCP:LISTEN | xargs -r kill
lsof -ti:8731 -sTCP:LISTEN | xargs -r kill
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document AI chat assistant env vars"
```
