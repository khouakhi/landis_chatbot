"""Tests for association code recognition and answer formatting."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from landis import codes
from landis.answer_format import tidy_answer_format
from landis.rag import SourceRef
from landis.services import chat_service

RECORDS = [
    ("0220", "Wick association"),
    ("0313a", "Hodnet association"),
    ("0541m", "South Petherton association"),
    ("0813a", "Midelney association"),
    ("0813b", "Fladbury 1 association"),
    ("0813c", "Fladbury 2 association"),
    ("0813d", "Fladbury 3 association"),
    ("0813e", "Compton association"),
    ("0813f", "Wallasea 1 association"),
    ("0813g", "Wallasea 2 association"),
    ("0813h", "Dowels association"),
    ("1013a", "Crowdy 1 association"),
    ("1013b", "Crowdy 2 association"),
]


# --- Code recognition -------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "what is association 0813h",
        "what is soil association 813h",
        "what is asssociation 0813h",
        "what is association 813H",
        "tell me about 813h",
    ],
)
def test_exact_code_with_or_without_zero_or_typo(question):
    match = codes.match_association(question, RECORDS)
    assert match is not None and match.kind == "exact"
    assert match.chunk_id == "KB-103-A-0813h"


def test_four_digit_code_and_code_without_letter():
    assert codes.match_association("what is association 1013a", RECORDS).chunk_id == (
        "KB-103-A-1013a"
    )
    assert codes.match_association("what is association 220", RECORDS).chunk_id == (
        "KB-103-A-0220"
    )


def test_number_without_letter_is_ambiguous_and_lists_every_letter():
    match = codes.match_association("what is soil association 813", RECORDS)
    assert match.kind == "ambiguous"
    assert [code for code, _ in match.choices][0] == "0813a"
    assert ("0813h", "Dowels association") in match.choices
    assert len(match.choices) == 8


def test_unknown_letter_offers_the_same_number():
    match = codes.match_association("what is association 0813z", RECORDS)
    assert match.kind == "ambiguous"
    assert len(match.choices) == 8


def test_unknown_number_offers_close_codes_only_when_association_is_named():
    named = codes.match_association("what is association 8134", RECORDS)
    assert named is not None and named.kind == "near"
    assert len(named.choices) <= 3
    assert codes.match_association("what is 8134", RECORDS) is None


@pytest.mark.parametrize(
    "question",
    [
        "what did Hartnup (1975) say about soils",
        "it covers 3,469 km2 or 2.29%",
        "what is a soil association",
        "what is the Denchworth series",
        "tell me about 813",
    ],
)
def test_text_without_a_code_is_ignored(question):
    assert codes.match_association(question, RECORDS) is None


def test_name_is_read_from_the_record_text():
    assert codes._name("Dowels (map unit 813h) is a soil association in the") == (
        "Dowels association"
    )
    assert codes._name("Fladbury 1 (map unit 813b) is a soil association") == (
        "Fladbury 1 association"
    )
    assert codes._name("") == ""


def test_clarification_lists_choices_and_offers_follow_up_questions():
    match = codes.match_association("what is association 813", RECORDS)
    reply, suggestions = codes.clarification(match)
    assert "Did you mean one of these?" in reply
    assert "- 0813h: Dowels association" in reply
    assert suggestions[0] == "What is association 0813a?"
    assert len(suggestions) == 8


def test_clarification_for_a_single_choice_asks_a_direct_question():
    match = codes.CodeMatch("ambiguous", "313", choices=(("0313a", "Hodnet association"),))
    reply, suggestions = codes.clarification(match)
    assert reply == (
        "I could not find an association with the code 313. "
        "Did you mean 0313a, the Hodnet association?"
    )
    assert suggestions == ["What is association 0313a?"]


# --- Chat service -----------------------------------------------------------


@pytest.fixture()
def service(monkeypatch):
    monkeypatch.setattr(codes, "association_records", lambda: RECORDS)
    monkeypatch.setattr(chat_service, "log_interaction", lambda **kwargs: None)
    calls = {}
    fake_source = SourceRef(
        label=1, chunk_id="KB-103-A-0813h", doc_id="KB-103", source_path="x",
        title="Export", section="Soil associations > 813h", location_start=0,
        location_end=0, preview="p", public_url=None, is_public=False,
        authority_tier="LandIS definitive", superseded=False, publication_year=None,
    )

    def fake_answer(text, **kwargs):
        calls["pinned"] = kwargs.get("pinned_chunk_ids")
        return SimpleNamespace(answer="Dowels is an association [1].", sources=[fake_source], top_k=8)

    monkeypatch.setattr(chat_service, "answer_question", fake_answer)
    return chat_service.ChatService(), calls


def test_ambiguous_code_asks_before_answering(service):
    chat, calls = service
    response = chat.chat("what is soil association 813")
    assert "Did you mean one of these?" in response.answer
    assert len(response.suggested_questions) == 8
    assert "pinned" not in calls  # the model was not called


def test_exact_code_is_pinned_for_retrieval(service):
    chat, calls = service
    response = chat.chat("what is association 813h")
    assert calls["pinned"] == ["KB-103-A-0813h"]
    assert "Dowels" in response.answer


def test_question_without_a_code_is_not_pinned(service):
    chat, calls = service
    chat.chat("what is a soil association")
    assert calls["pinned"] == []


def test_database_error_does_not_block_the_chat(monkeypatch):
    def broken():
        raise RuntimeError("no database")

    monkeypatch.setattr(codes, "association_records", broken)
    assert codes.find_association("what is association 813h") is None


# --- Answer formatting ------------------------------------------------------


def test_bold_sentence_is_unbolded_and_colon_removed():
    answer = (
        "### Definition of association 1013a\n"
        "**Soil association 1013a is called Crowdy 1.**:\n"
        "**It is a peat soil association on the National Soil Map of England and Wales.:**\n"
        "It covers about 213 km2, which is 0.14% of the mapped area.: [1]"
    )
    assert tidy_answer_format(answer) == (
        "### Definition of association 1013a\n"
        "Soil association 1013a is called Crowdy 1.\n"
        "It is a peat soil association on the National Soil Map of England and Wales.\n"
        "It covers about 213 km2, which is 0.14% of the mapped area. [1]"
    )


def test_bold_title_is_kept_and_bullet_labels_are_plain():
    answer = (
        "**Composition and proportions:**\n"
        "- **Size:** The association covers about 213 km2 [1].\n"
        "- **Main soils:** Peat soils [1]."
    )
    assert tidy_answer_format(answer) == (
        "**Composition and proportions:**\n"
        "- Size: The association covers about 213 km2 [1].\n"
        "- Main soils: Peat soils [1]."
    )


def test_headings_are_untouched_and_table_cells_are_plain():
    answer = "### What is it?\n| a | b |\n| - | - |\n| **x.** | y. |"
    assert tidy_answer_format(answer) == "### What is it?\n| a | b |\n| - | - |\n| x. | y. |"


def test_lone_citation_marker_joins_the_line_above():
    answer = "- **Area:** About 213 km2.\n\n[1]"
    assert tidy_answer_format(answer) == "- Area: About 213 km2. [1]"


def test_ensure_citation_adds_a_missing_marker_once():
    from landis.citations import ensure_citation

    assert ensure_citation("- Area: 213 km2", 1) == "- Area: 213 km2 [1]"
    assert ensure_citation("- Area: 213 km2 [1]", 1) == "- Area: 213 km2 [1]"
    assert ensure_citation("- Area [2, 1]", 1) == "- Area [2, 1]"


# --- Soils Guide link -------------------------------------------------------


def _source(label, url=None, title=None):
    return SourceRef(
        label=label, chunk_id=f"c{label}", doc_id="KB-103", source_path="x", title="Export",
        section="s", location_start=0, location_end=0, preview="p", record_url=url,
        record_title=title,
    )


def test_more_detail_line_links_cited_records():
    from landis.citations import more_detail_line

    sources = [
        _source(1, "https://www.landis.org.uk/soilsguide/mapunit.cfm?mu=81308", "Dowels association"),
        _source(2, None),
        _source(3, "https://www.landis.org.uk/soilsguide/mapunit.cfm?mu=81308", "Dowels association"),
        _source(4, "https://www.landis.org.uk/soilsguide/index.cfm", None),
    ]
    assert more_detail_line(sources) == (
        "More detail is in the LandIS Soils Guide: "
        "[Dowels association](https://www.landis.org.uk/soilsguide/mapunit.cfm?mu=81308), "
        "[LandIS Soils Guide](https://www.landis.org.uk/soilsguide/index.cfm)."
    )


def test_more_detail_line_is_empty_without_record_links():
    from landis.citations import more_detail_line

    assert more_detail_line([_source(1)]) == ""
    assert more_detail_line([]) == ""


# --- Several codes and comparisons -------------------------------------------

MULTI_RECORDS = RECORDS + [("0512e", "Block association"), ("1024c", "Adventurers' 3 association")]


def test_every_code_in_a_comparison_is_pinned():
    pinned, needs_choice = [], None
    matches = codes.match_all_associations(
        "How do the land-drainage needs of 512e compare with those of 1024c?", MULTI_RECORDS
    )
    assert [m.chunk_id for m in matches] == ["KB-103-A-0512e", "KB-103-A-1024c"]
    assert all(m.kind == "exact" for m in matches)


def test_find_associations_pins_each_code_once(monkeypatch):
    monkeypatch.setattr(codes, "association_records", lambda: MULTI_RECORDS)
    pinned, needs_choice = codes.find_associations("compare 512e with 1024c and 512e again")
    assert pinned == ["KB-103-A-0512e", "KB-103-A-1024c"]
    assert needs_choice is None


def test_ambiguous_code_in_a_comparison_still_asks(monkeypatch):
    monkeypatch.setattr(codes, "association_records", lambda: MULTI_RECORDS)
    pinned, needs_choice = codes.find_associations("compare 813 with 1024c")
    assert pinned == []
    assert needs_choice.kind == "ambiguous"


def test_comparison_is_detected_and_definitions_are_not():
    from landis.rag import looks_like_comparison

    assert looks_like_comparison("How do X compare with Y?")
    assert looks_like_comparison("what is the difference between 512e and 1024c")
    assert looks_like_comparison("512e versus 1024c")
    assert not looks_like_comparison("what is association 1013a")
    assert not looks_like_comparison("what is the area of the Denchworth series")


def test_comparison_and_multi_record_notes_reach_the_model(monkeypatch):
    from types import SimpleNamespace

    from landis import rag

    captured = {}

    class FakeChat:
        def complete(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            message = SimpleNamespace(content="A table [1] [2].")
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=FakeChat())
    monkeypatch.setattr(rag, "retrieve", lambda client, question, top_k, pinned=(): ([], "ctx"))
    monkeypatch.setattr(rag.emb, "get_client", lambda: fake_client)
    rag.answer_question(
        "How do the land-drainage needs of 512e compare with those of 1024c?",
        pinned_chunk_ids=["KB-103-A-0512e", "KB-103-A-1024c"],
    )
    user_block = captured["messages"][-1]["content"]
    assert "2 associations by their codes" in user_block
    assert "Markdown table" in user_block


def test_model_written_more_detail_line_is_removed():
    from landis.citations import strip_more_detail_lines

    answer = (
        "Block is a soil association [1].\n\n"
        "More detail is in the LandIS Soils Guide: Block association.\n\n"
        "**More detail is in the LandIS Soils Guide:** [Block](https://x).\n"
    )
    assert strip_more_detail_lines(answer) == "Block is a soil association [1]."


def test_ensure_citation_skips_table_rows():
    from landis.citations import ensure_citation

    table = "Compare:\n\n| Map unit | Need |\n| - | - |\n| 512e | little |\n| 1024c | artificial |"
    result = ensure_citation(table, 2)
    assert "| 1024c | artificial |" in result.split("\n")
    assert "| 512e | little |" in result.split("\n")
    assert "Compare: [2]" in result.split("\n")


# --- Keyword search query -----------------------------------------------------


def test_keyword_query_keeps_content_words_and_joins_with_or():
    from landis.store_pg import keyword_query

    assert keyword_query("how many units do Soilscapes define?") == "units | soilscapes"
    assert keyword_query("What is the area of the Denchworth series?") == (
        "area | denchworth | series"
    )


def test_keyword_query_is_empty_for_stopwords_only():
    from landis.store_pg import keyword_query

    assert keyword_query("what is it?") == ""


# --- Bold only where it is explicit -----------------------------------------


def test_inline_bold_in_a_sentence_is_removed():
    answer = "The soils are **always wet** and **very acid** [1]."
    assert tidy_answer_format(answer) == "The soils are always wet and very acid [1]."


def test_bullet_labels_lose_their_bold():
    answer = "- **Class 1:** Free draining soils [4].\n**Land use:** Grassland [2]."
    assert tidy_answer_format(answer) == (
        "- Class 1: Free draining soils [4].\nLand use: Grassland [2]."
    )


def test_bold_section_title_on_its_own_line_is_kept():
    assert tidy_answer_format("**Drainage status:**") == "**Drainage status:**"
    assert tidy_answer_format("**Altcar 50%:**") == "**Altcar 50%:**"


def test_bold_component_bullet_becomes_a_heading():
    assert tidy_answer_format("- **Altcar 50%:**") == "**Altcar 50%:**"


def test_table_cell_bold_is_removed_and_structure_kept():
    table = "| Map unit | Need |\n| --- | --- |\n| **512e** | little |"
    assert tidy_answer_format(table) == "| Map unit | Need |\n| --- | --- |\n| 512e | little |"


def test_plain_short_heading_is_made_bold():
    assert tidy_answer_format("Location and extent:") == "**Location and extent:**"
    assert tidy_answer_format("Component series and their proportions:") == (
        "**Component series and their proportions:**"
    )


def test_bullet_with_only_a_label_becomes_a_bold_heading():
    assert tidy_answer_format("- Overview:\n  Altcar 2 is a soil association.") == (
        "**Overview:**\n  Altcar 2 is a soil association."
    )


def test_plain_heading_rule_leaves_bullets_codes_and_sentences_alone():
    answer = (
        "- Altcar 50%: 111 km2 of the area [1].\n"
        "HOST12 Undrained lowland peaty soils waterlogged by groundwater:\n"
        "Soils are wet for most of the year, which limits the crops that can be grown:\n"
        "Dominant soils: Deep peat soils [1]."
    )
    assert tidy_answer_format(answer) == answer


def test_bold_label_only_bullet_becomes_a_heading():
    assert tidy_answer_format("- **Overview:**\n  Altcar 2 is a soil association.") == (
        "**Overview:**\n  Altcar 2 is a soil association."
    )
    assert tidy_answer_format("- **Size:** 213 km2 [1].") == "- Size: 213 km2 [1]."

