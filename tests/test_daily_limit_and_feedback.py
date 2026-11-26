"""Tests for audience, format, and language prompts, the daily limit, and feedback."""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from landis import config as cfg
from landis import daily_limit
from landis.feedback import REASONS, feedback_path, save_feedback
from landis.rag import FORMAT_TAGS, LANGUAGES, system_prompt


# --- Prompt rules -----------------------------------------------------------


def test_default_prompt_keeps_citation_and_guardrail_rules():
    prompt = system_prompt()
    assert "[1] or [2, 3]" in prompt
    assert "Do not give site-specific advice" in prompt
    assert "Do not interpret regulations" in prompt
    assert "<<SUGGESTED>>" in prompt and "<<END>>" in prompt
    assert "Audience: general public" in prompt
    assert "Write the whole answer in English" in prompt


def test_audience_notes_are_distinct():
    general = system_prompt("general")
    manager = system_prompt("manager")
    specialist = system_prompt("specialist")
    assert "plain English" in general
    assert "land manager or adviser" in manager
    assert "soil specialist" in specialist
    assert general != manager != specialist


def test_unknown_audience_falls_back_to_general():
    assert system_prompt("nonsense") == system_prompt("general")


def test_format_tags_add_matching_instructions_only():
    prompt = system_prompt("general", ["table", "steps"])
    assert "Markdown table" in prompt
    assert "numbered steps" in prompt
    assert "keep the answer short" not in prompt
    assert "compare the options" not in prompt.lower()


def test_every_format_tag_has_an_instruction():
    for tag in FORMAT_TAGS:
        base = system_prompt("general", [])
        assert system_prompt("general", [tag]) != base


def test_welsh_prompt_asks_for_welsh_and_keeps_block():
    prompt = system_prompt("general", [], "cy")
    assert "Welsh (Cymraeg)" in prompt
    assert "Write the whole answer in English" not in prompt
    assert "<<SUGGESTED>>" in prompt
    assert set(LANGUAGES) == {"en", "cy"}


def test_style_notes_say_they_do_not_change_sources():
    prompt = system_prompt("specialist", ["compare"], "en")
    assert "not which sources are used" in prompt


# --- Feedback file ----------------------------------------------------------


def test_feedback_is_appended_as_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "FEEDBACK_DIR", tmp_path / "feedback")
    when = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)
    record = {"rating": "down", "reason": "too_technical", "question": "Q?", "answer": "A"}

    first = save_feedback(record, when=when)
    save_feedback({**record, "rating": "up", "reason": None}, when=when)

    assert first == tmp_path / "feedback" / "2026-11-22.jsonl"
    lines = first.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["reason"] == "too_technical"
    assert json.loads(lines[1])["rating"] == "up"


def test_feedback_path_uses_utc_day(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "FEEDBACK_DIR", tmp_path)
    when = datetime(2026, 1, 5, 23, 30, tzinfo=timezone.utc)
    assert feedback_path(when).name == "2026-01-05.jsonl"


def test_feedback_reasons_match_the_brief():
    assert REASONS == ("incorrect", "not_answered", "too_technical", "too_simple")


# --- Daily limit ------------------------------------------------------------


class _FakeCounter:
    """In-memory stand-in for the landis_daily_usage table."""

    def __init__(self):
        self.rows: dict[tuple[date, str], int] = {}

    def get(self, usage_date, key):
        return self.rows.get((usage_date, key), 0)

    def consume(self, usage_date, key, limit):
        count = self.rows.get((usage_date, key), 0)
        if count >= limit:
            return None
        self.rows[(usage_date, key)] = count + 1
        return count + 1


@pytest.fixture
def counter(monkeypatch):
    fake = _FakeCounter()
    monkeypatch.setattr(daily_limit.store_pg, "get_daily_question_count", fake.get)
    monkeypatch.setattr(daily_limit.store_pg, "consume_daily_question", fake.consume)
    monkeypatch.setattr(cfg, "DAILY_QUESTION_LIMIT", 3)
    return fake


def test_limit_counts_up_then_refuses(counter):
    token = daily_limit.new_visitor_token()
    results = [daily_limit.consume_question(token) for _ in range(4)]
    assert [r.used for r in results[:3]] == [1, 2, 3]
    assert results[3] is None
    assert daily_limit.current_usage(token).remaining == 0


def test_visitors_are_counted_separately(counter):
    first = daily_limit.new_visitor_token()
    second = daily_limit.new_visitor_token()
    for _ in range(3):
        daily_limit.consume_question(first)
    assert daily_limit.consume_question(second).used == 1


def test_stored_key_is_a_hash_not_the_cookie(counter):
    token = daily_limit.new_visitor_token()
    daily_limit.consume_question(token)
    stored_keys = {key for (_day, key) in counter.rows}
    assert token not in stored_keys
    assert daily_limit.visitor_key(token) in stored_keys
    assert len(daily_limit.visitor_key(token)) == 64


def test_token_validation():
    assert daily_limit.is_valid_visitor_token(daily_limit.new_visitor_token())
    assert not daily_limit.is_valid_visitor_token(None)
    assert not daily_limit.is_valid_visitor_token("short")
    assert not daily_limit.is_valid_visitor_token("bad token with spaces!!")


# --- HTTP routes (needs httpx; skipped if not installed) --------------------


def _api_client(monkeypatch):
    httpx = pytest.importorskip("httpx")  # noqa: F841 (TestClient needs it)
    from fastapi.testclient import TestClient

    api_dir = Path(__file__).resolve().parents[1] / "apps" / "api"
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))
    import main  # noqa: PLC0415

    return TestClient(main.app)


def test_chat_refuses_at_limit_without_calling_model(monkeypatch, counter):
    client = _api_client(monkeypatch)
    from routes import chat as chat_routes

    called = {"n": 0}

    def fake_chat(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("model must not be called at the limit")

    monkeypatch.setattr(chat_routes.chat_service, "chat", fake_chat)
    client.cookies.set(cfg.VISITOR_COOKIE_NAME, daily_limit.new_visitor_token())
    token = client.cookies.get(cfg.VISITOR_COOKIE_NAME)
    key = daily_limit.visitor_key(token)
    counter.rows[(daily_limit.today_utc(), key)] = 3

    response = client.post("/api/chat", json={"message": "What is Soilscapes?"})
    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == "daily_limit"
    assert detail["remaining"] == 0
    assert called["n"] == 0


def test_chat_passes_audience_formats_and_lang(monkeypatch, counter):
    client = _api_client(monkeypatch)
    from landis.services import ChatResponse
    from routes import chat as chat_routes

    seen = {}

    def fake_chat(text, **kwargs):
        seen.update(kwargs)
        return ChatResponse(answer="Ateb [1]", sources=[], top_k=8)

    monkeypatch.setattr(chat_routes.chat_service, "chat", fake_chat)
    response = client.post(
        "/api/chat",
        json={
            "message": "Beth yw Soilscapes?",
            "audience": "manager",
            "formats": ["table", "table", "steps"],
            "lang": "cy",
        },
    )
    assert response.status_code == 200
    assert seen["audience"] == "manager"
    assert seen["formats"] == ["table", "steps"]
    assert seen["lang"] == "cy"
    assert response.json()["usage"]["used"] == 1
    assert "landis_visitor" in response.headers.get("set-cookie", "")


def test_chat_rejects_unknown_audience_and_format(monkeypatch, counter):
    client = _api_client(monkeypatch)
    bad_audience = client.post("/api/chat", json={"message": "hi", "audience": "pupil"})
    bad_format = client.post("/api/chat", json={"message": "hi", "formats": ["#poem"]})
    assert bad_audience.status_code == 422
    assert bad_format.status_code == 422


def test_feedback_rejects_reason_on_thumbs_up(monkeypatch):
    client = _api_client(monkeypatch)
    response = client.post(
        "/api/feedback",
        json={"rating": "up", "reason": "incorrect", "question": "Q"},
    )
    assert response.status_code == 422


def test_feedback_writes_record_without_ip(monkeypatch, tmp_path):
    client = _api_client(monkeypatch)
    monkeypatch.setattr(cfg, "FEEDBACK_DIR", tmp_path)
    response = client.post(
        "/api/feedback",
        headers={"User-Agent": "pytest-agent", "X-Forwarded-For": "203.0.113.9"},
        json={
            "rating": "down",
            "reason": "too_simple",
            "question": "What is HOST?",
            "answer": "Answer text",
            "audience": "specialist",
            "formats": ["bullets"],
            "lang": "en",
            "cited_chunk_ids": ["abc", "def"],
            "response_ms": 3200,
        },
    )
    assert response.status_code == 201
    files = list(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    record = json.loads(files[0].read_text(encoding="utf-8").strip())
    assert record["reason"] == "too_simple"
    assert record["cited_chunk_ids"] == ["abc", "def"]
    assert record["response_ms"] == 3200
    serialised = json.dumps(record)
    assert "203.0.113.9" not in serialised
    assert "pytest-agent" not in serialised


# --- Citation markers -------------------------------------------------------


def test_clean_citation_markers_drops_association_codes():
    from landis.citations import clean_citation_markers

    text = "Groups series [3, 4, 541m, 654a, 711] together [541m] and [2]."
    cleaned = clean_citation_markers(text, 8)
    assert cleaned == "Groups series [3, 4] together and [2]."


def test_clean_citation_markers_leaves_other_brackets_alone():
    from landis.citations import clean_citation_markers

    text = "See [the portal](https://example.org) and [Table 1]."
    assert clean_citation_markers(text, 5) == text


def test_prompt_forbids_codes_in_citation_brackets():
    assert "541m" in system_prompt()


def test_clean_citation_markers_keeps_at_most_two():
    from landis.citations import clean_citation_markers

    assert clean_citation_markers("A fact [1, 3, 4, 5, 6].", 8) == "A fact [1, 3]."


def test_definition_question_detected():
    from landis.rag import looks_like_definition

    assert looks_like_definition("what is soil associations")
    assert looks_like_definition("What are soil associations?")
    assert looks_like_definition("define soil series")
    assert not looks_like_definition("what is the area of the Denchworth series")


def test_definition_note_added_to_prompt_context(monkeypatch):
    from types import SimpleNamespace

    from landis import rag

    captured = {}

    class FakeChat:
        def complete(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            message = SimpleNamespace(content="A definition [1].")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=FakeChat())
    monkeypatch.setattr(rag, "retrieve", lambda client, question, top_k, pinned=(): ([], "[1] excerpt"))
    monkeypatch.setattr(rag.emb, "get_client", lambda: fake_client)
    rag.answer_question("what is soil associations")
    user_block = captured["messages"][-1]["content"]
    assert "definition question" in user_block
