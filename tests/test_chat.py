# tests/test_chat.py — Tests for routes/chat.py (AI chat assistant)
#
# Uses SQLite in-memory for speed, same pattern as tests/test_identity.py.
# Route/helper functions are called directly as plain Python functions
# rather than through FastAPI's TestClient, so importing this module never
# triggers the app's lifespan (init_db / start_scheduler).

import json
import logging
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import anthropic
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fake_message = SimpleNamespace(content=[SimpleNamespace(type="text", text="mocked reply")])
    mock_client = MagicMock()
    mock_client.beta.messages.tool_runner.return_value = [fake_message]

    with patch("routes.chat.anthropic.Anthropic", return_value=mock_client):
        # Realistic alternating conversation ending on the user's latest
        # message, same shape the frontend actually sends.
        long_history = [
            ChatMessage(role="user" if i % 2 == 0 else "assistant", content=f"message {i}")
            for i in range(21)
        ]
        req = ChatRequest(password="correct-horse", messages=long_history)
        result = chat(req, db)

    assert result.reply == "mocked reply"

    sent_messages = mock_client.beta.messages.tool_runner.call_args.kwargs["messages"]
    assert sent_messages[0]["role"] == "user"
    assert len(sent_messages) <= 12
    assert sent_messages[-1]["content"] == "message 20"


def test_chat_falls_back_when_no_text_block(monkeypatch, db):
    from routes.chat import ChatMessage, ChatRequest, chat

    monkeypatch.setenv("CHAT_PASSWORD", "correct-horse")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

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


# =============================================================================
# Fix-pass tests (from final review findings)
# =============================================================================

def test_trim_history_keeps_first_message_as_user_role():
    # Real conversations always alternate user/assistant and end on the
    # user turn just sent (that's when the frontend POSTs). A naive
    # messages[-N:] slice of an odd-length alternating list can land on
    # an assistant message first, which the API rejects outright.
    from routes.chat import _trim_history

    messages = []
    for i in range(21):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": f"m{i}"})

    result = _trim_history(messages, max_history=12)

    assert result[0]["role"] == "user"
    assert len(result) <= 12
    assert result[-1]["content"] == "m20"


def test_trim_history_short_conversation_untouched():
    from routes.chat import _trim_history

    messages = [{"role": "user", "content": "hi"}]

    result = _trim_history(messages, max_history=12)

    assert result == messages


def test_query_inventory_condition_filter_is_substring_match(db):
    # Real inventory data never has a bare "Used"/"New"/"Refurbished"
    # condition value -- it's free text like "Select / Good / Pass". An
    # exact-match filter can never match anything real.
    from routes.chat import _query_inventory

    _make_inventory(db, condition="Select / Good / Pass")
    _make_inventory(db, condition="Part Complete / Fair")

    result = _query_inventory(db, condition="Pass")

    assert result["count"] == 1


def test_query_inventory_excludes_unknown_meter_from_max_meter_filter(db):
    # A record with no meter reading at all must not silently pass a
    # "under X meters" ceiling -- the ceiling can't be confirmed.
    from routes.chat import _query_inventory

    _make_inventory(db, total_meter=50000)
    _make_inventory(db, total_meter=None)

    result = _query_inventory(db, max_meter=60000)

    assert result["count"] == 1
    assert result["records"][0]["total"] == 50000


def test_query_inventory_excludes_unknown_price_from_max_price_filter(db):
    from routes.chat import _query_inventory

    _make_inventory(db, price=2000)
    _make_inventory(db, price=None)

    result = _query_inventory(db, max_price=3000)

    assert result["count"] == 1
    assert result["records"][0]["price"] == 2000


def test_query_inventory_color_filter_is_case_insensitive(db):
    from routes.chat import _query_inventory

    _make_inventory(db, is_color="YES")
    _make_inventory(db, is_color="NO")

    result = _query_inventory(db, color="yes")

    assert result["count"] == 1
    assert result["records"][0]["isColor"] == "YES"


def test_query_inventory_records_omit_free_text_fields(db):
    # description/notes are free text scraped from third-party wholesaler
    # sites and cost real tokens per call; they're also the only path by
    # which untrusted scraped content could reach the model's context in
    # the same turn as write-capable tools. Drop them from what the tool
    # returns.
    from routes.chat import _query_inventory

    _make_inventory(db, description="some scraped text", notes="some scraped notes")

    result = _query_inventory(db, brand="Canon")

    assert "description" not in result["records"][0]
    assert "notes" not in result["records"][0]
    assert result["records"][0]["brand"] == "Canon"


def test_chat_missing_api_key_returns_clean_500(monkeypatch, db):
    from routes.chat import ChatMessage, ChatRequest, chat

    monkeypatch.setenv("CHAT_PASSWORD", "correct-horse")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    req = ChatRequest(password="correct-horse", messages=[ChatMessage(role="user", content="hi")])

    with pytest.raises(HTTPException) as exc_info:
        chat(req, db)

    assert exc_info.value.status_code == 500
    assert "ANTHROPIC_API_KEY" in exc_info.value.detail


def test_chat_passes_max_iterations_and_timeout_to_tool_runner(monkeypatch, db):
    from routes.chat import ChatMessage, ChatRequest, chat

    monkeypatch.setenv("CHAT_PASSWORD", "correct-horse")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    fake_message = SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")])
    mock_client = MagicMock()
    mock_client.beta.messages.tool_runner.return_value = [fake_message]

    with patch("routes.chat.anthropic.Anthropic", return_value=mock_client):
        req = ChatRequest(password="correct-horse", messages=[ChatMessage(role="user", content="hi")])
        chat(req, db)

    call_kwargs = mock_client.beta.messages.tool_runner.call_args.kwargs
    assert call_kwargs["max_iterations"] == 10
    assert call_kwargs["timeout"] == 90


def test_update_watchlist_item_docstring_warns_about_clearing_fields():
    # None means "leave this field alone", so this tool can never clear a
    # field -- only replace it. The model needs to know that so it
    # doesn't claim success for an impossible "remove the price cap"
    # request.
    from routes.chat import _make_tools

    tools = _make_tools(db=MagicMock())
    update_tool = next(t for t in tools if t.name == "update_watchlist_item")

    assert "cannot" in update_tool.description.lower() or "clear" in update_tool.description.lower()


def test_chat_logs_upstream_error_before_raising(monkeypatch, db, caplog):
    from routes.chat import ChatMessage, ChatRequest, chat

    monkeypatch.setenv("CHAT_PASSWORD", "correct-horse")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    mock_client = MagicMock()
    mock_client.beta.messages.tool_runner.side_effect = anthropic.APIConnectionError(request=MagicMock())

    with patch("routes.chat.anthropic.Anthropic", return_value=mock_client):
        with caplog.at_level(logging.ERROR):
            req = ChatRequest(password="correct-horse", messages=[ChatMessage(role="user", content="hi")])
            with pytest.raises(HTTPException):
                chat(req, db)

    assert any("Claude" in r.message or "Anthropic" in r.message for r in caplog.records)
