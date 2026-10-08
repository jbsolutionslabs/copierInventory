# routes/chat.py — POST /api/chat, an AI assistant with read access to
# live inventory and read/write (no delete) access to the customer
# watchlist, backed by Claude Sonnet 5 tool use.

import hmac
import json
import logging
import os

import anthropic
from anthropic import beta_tool
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from aggregator import _find_matches
from db import InventoryRecord, ScrapeRun, WatchlistItem, get_db
from routes.inventory import _record_to_dict
from routes.watchlist import (
    WatchlistItemIn,
    _orm_to_dict,
    add_watchlist_item as _wl_add_route,
    update_watchlist_item as _wl_update_route,
)

log = logging.getLogger(__name__)

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
    them) are applied as additional local filtering.

    Real inventory data has no clean "Used"/"New"/"Refurbished" vocabulary
    (condition is free text like "Select / Good / Pass") and is frequently
    missing meter/price entirely, so: condition matches by substring, and
    a missing meter reading is excluded from a max_meter ceiling rather
    than silently passing it (max_price already excludes unknowns inside
    _find_matches). color is uppercased before being handed to
    _find_matches, whose comparison is case-sensitive.
    """
    last_run = (
        db.query(ScrapeRun)
        .filter(ScrapeRun.status == "success")
        .order_by(ScrapeRun.id.desc())
        .first()
    )
    run_started_at = last_run.started_at if last_run else None
    all_records = db.query(InventoryRecord).all()
    inventory_json = [_record_to_dict(r, run_started_at) for r in all_records]
    # _record_to_dict collapses a missing meter reading to 0 (same as a
    # genuine zero), so track the raw value separately by object identity
    # -- _find_matches returns the same dict objects it was given.
    raw_meter_by_id = {id(d): rec.total_meter for rec, d in zip(all_records, inventory_json)}

    req = {
        "brand": brand or "",
        "model": model_contains or "",
        "color": (color or "").upper(),
        "state": state or "",
        "maxMeter": max_meter,
        "maxPrice": max_price,
    }
    matches = _find_matches(req, inventory_json)

    if max_meter is not None:
        matches = [m for m in matches if raw_meter_by_id.get(id(m)) is not None]
    if min_meter is not None:
        matches = [m for m in matches if (m.get("total") or 0) >= min_meter]
    if condition:
        needle = condition.strip().lower()
        matches = [m for m in matches if needle in (m.get("condition") or "").lower()]

    # Drop free-text fields scraped from third-party wholesaler sites --
    # they cost tokens on every call and are the only path by which
    # untrusted scraped content could reach the model's context in the
    # same turn as the write-capable watchlist tools.
    trimmed = [
        {k: v for k, v in m.items() if k not in ("description", "notes")}
        for m in matches
    ]

    return {"count": len(trimmed), "records": trimmed[:100]}


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


MAX_HISTORY = 12


def _trim_history(messages: list[dict], max_history: int = MAX_HISTORY) -> list[dict]:
    """Keep the last max_history messages, then drop any leading
    non-user messages. A real conversation alternates user/assistant and
    always ends on a user turn (that's when a request fires), so taking
    an even-sized tail of an odd-length list can otherwise land on an
    assistant message first -- the API requires the first message to be
    from the user and rejects the request outright if it isn't."""
    trimmed = messages[-max_history:]
    while trimmed and trimmed[0]["role"] != "user":
        trimmed.pop(0)
    return trimmed

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
    "- When asked to add a new request for a named customer, also call "
    "get_watchlist first to check whether they already have an entry on "
    "file, before adding a new request for them. If they do, use their "
    "exact existing name (not the name as the user typed it) so the new "
    "request groups with their other requests instead of creating a "
    "duplicate customer.\n"
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
            condition: Substring match against condition, e.g. "Pass" or "Fair".
                Condition is free text (e.g. "Select / Good / Pass") and is
                blank on most records -- don't assume a clean vocabulary.
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
        """Update fields on an existing customer watchlist request. Only provided fields are changed -- you cannot clear a field this way, only replace it with a new value. If asked to clear/remove a field, say that has to be done manually in the Customer Watchlist tab.

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
    if not expected_password or not hmac.compare_digest(req.password, expected_password):
        raise HTTPException(status_code=401, detail="invalid passphrase")

    history = _trim_history([{"role": m.role, "content": m.content} for m in req.messages])
    if not history:
        # Used by the frontend to validate the passphrase without spending
        # an Anthropic API call.
        return ChatResponse(reply="")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(status_code=500, detail="server misconfigured: ANTHROPIC_API_KEY is not set")

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
            max_iterations=10,
            timeout=90,
        )
        last_message = None
        for message in runner:
            last_message = message
    except anthropic.AuthenticationError:
        log.error("Anthropic authentication failed -- check ANTHROPIC_API_KEY")
        raise HTTPException(status_code=500, detail="server misconfigured: invalid Anthropic API key")
    except anthropic.APIStatusError as e:
        log.error("Claude API error: %s", e.message)
        raise HTTPException(status_code=502, detail=f"Claude API error: {e.message}")
    except anthropic.APIConnectionError as e:
        log.error("Could not reach Claude API: %s", e)
        raise HTTPException(status_code=502, detail="could not reach Claude API")

    if last_message is None:
        raise HTTPException(status_code=502, detail="no response from Claude")

    reply = next((b.text for b in last_message.content if b.type == "text"), "")
    if not reply:
        reply = "I wasn't able to generate a response to that — try rephrasing your question."

    return ChatResponse(reply=reply)
