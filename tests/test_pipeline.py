"""Tests for the register, OCR layout, chunking, and citation rules."""

from __future__ import annotations

from landis.citations import sources_cited_in_answer, split_suggested_questions
from landis.indexing import document_record, skips_prose_chunking, uses_structured_chunks
from landis.markdown_tables import normalise_markdown_tables
from landis.metadata import apply_prefill
from landis.normalise import chunks_from_pages
from landis.ocr import OcrSkipped, prepare_row
from landis.ocr_layout import normalise_keys, snake_case, write_cleaned_document
from landis.ocr_request import batch_line, estimate_cost_usd, extract_ocr_payload
from landis.qa import review_document, review_page
from landis.rag import (
    SourceRef,
    apply_rank_boosts,
    looks_like_point_location,
    looks_like_regulatory,
    reciprocal_rank_fusion,
)
from landis.register import (
    classify_by_title,
    licence_allows_external_processing,
    load_register,
    normalise_row,
    ready_to_index,
    save_register,
    scan_knowledge_base,
    validate_row,
    worked_example_rows,
)


def test_worked_examples_match_the_october_register():
    rows = worked_example_rows()
    assert [row["doc_id"] for row in rows] == [f"D{i:02d}" for i in range(1, 11)]
    assert rows[0]["subject_primary"] == "1 Soil mapping units"
    assert rows[8]["licence"] == "unknown - check"
    assert all(not validate_row(row) for row in rows)


def test_licence_gate_blocks_unknown_and_restricted():
    assert licence_allows_external_processing("open")
    assert licence_allows_external_processing("LandIS licence")
    assert not licence_allows_external_processing("restricted - do not ingest")
    assert not licence_allows_external_processing("unknown - check")
    assert not licence_allows_external_processing("")


def test_controlled_values_are_canonical_and_rejected():
    row = normalise_row({"Doc ID": "D11", "Document type": "Brochure", "Publication year": "no"})
    assert row["document_type"] == "brochure"
    assert any("publication year" in problem for problem in validate_row(row))
    bad = normalise_row({"doc_id": "D12", "document_type": "survey rpt"})
    assert any("document_type" in problem for problem in validate_row(bad))


def test_scan_adds_a_file_once(tmp_path):
    (tmp_path / "notes.pdf").write_bytes(b"%PDF-1.4")
    first = scan_knowledge_base(tmp_path, [], extensions=(".pdf",))
    second = scan_knowledge_base(tmp_path, first, extensions=(".pdf",))
    assert len(first) == 1
    assert first[0]["doc_id"] == "KB-001"
    assert first[0]["licence"] == "unknown - check"
    assert len(second) == 1
    assert not ready_to_index(first[0])


def test_ingested_row_is_not_embedded_again_unless_forced():
    ingested = {
        "doc_id": "KB-006",
        "licence": "LandIS licence",
        "pipeline_status": "indexed",
        "ingest_status": "ingested",
    }
    assert not ready_to_index(ingested)
    assert not ready_to_index(ingested, include_unreviewed=True)
    assert ready_to_index(ingested, force=True)
    fresh = {
        "doc_id": "KB-007",
        "licence": "open",
        "pipeline_status": "cleaned",
        "ingest_status": "cleaned",
    }
    assert ready_to_index(fresh)


def test_scan_reads_a_type_subfolder_and_keeps_doc_type(tmp_path):
    folder = tmp_path / "01_Source_raw" / "08_Maps_and_figures"
    folder.mkdir(parents=True)
    (folder / "plate.jpg").write_bytes(b"\xff\xd8")
    (folder / ".gitkeep").write_text("", encoding="utf-8")
    rows = scan_knowledge_base(tmp_path, [], extensions=(".jpg", ".pdf"))
    assert len(rows) == 1
    assert rows[0]["file_name"] == "08_Maps_and_figures/plate.jpg"
    assert rows[0]["doc_type"] == "map"
    again = scan_knowledge_base(tmp_path, rows, extensions=(".jpg", ".pdf"))
    assert len(again) == 1


def test_inbox_file_is_placed_from_its_title(tmp_path):
    inbox = tmp_path / "01_Source_raw" / "00_Inbox"
    inbox.mkdir(parents=True)
    (inbox / "Soil_Glossary.pdf").write_bytes(b"%PDF-1.4")
    (inbox / "field_notes.pdf").write_bytes(b"%PDF-1.4")
    (inbox / "structure_brochure.pdf").write_bytes(b"%PDF-1.4")
    rows = scan_knowledge_base(tmp_path, [], extensions=(".pdf",))
    by_title = {row["title"]: row for row in rows}
    glossary = by_title["Soil Glossary"]
    assert glossary["doc_type"] == "reference"
    assert glossary["file_name"] == (
        f"02_Reference_and_classification/{glossary['doc_id']}_Soil_Glossary.pdf"
    )
    assert not (inbox / "Soil_Glossary.pdf").exists()
    assert (tmp_path / "01_Source_raw" / glossary["file_name"]).is_file()
    assert by_title["field notes"]["doc_type"] == ""
    assert by_title["field notes"]["file_name"] == "00_Inbox/field_notes.pdf"
    assert (inbox / "field_notes.pdf").is_file()
    brochure = by_title["structure brochure"]
    assert brochure["doc_type"] == "guidance"
    assert brochure["file_name"] == (
        f"06_Guidance_and_brochures/{brochure['doc_id']}_structure_brochure.pdf"
    )
    again = scan_knowledge_base(tmp_path, rows, extensions=(".pdf",))
    assert len(again) == 3


def test_tool_note_is_filed_and_copied_without_ocr(tmp_path):
    inbox = tmp_path / "01_Source_raw" / "00_Inbox"
    inbox.mkdir(parents=True)
    (inbox / "TN-004_soil_classification.md").write_text("A draft note.\n", encoding="utf-8")
    report: list[str] = []
    rows = scan_knowledge_base(tmp_path, [], extensions=(".md", ".pdf"), report=report)
    assert len(rows) == 1
    row = rows[0]
    assert row["doc_type"] == "tool_note"
    assert row["authority_tier"] == "LandIS definitive"
    assert row["pipeline_status"] == "needs_review"
    assert row["qa_flags"] == "draft, needs LandIS sign-off"
    assert row["file_name"] == (
        f"07_Portal_web_content/Tool_notes/{row['doc_id']}_TN-004_soil_classification.md"
    )
    assert not (inbox / "TN-004_soil_classification.md").exists()
    copied = tmp_path / "02_Source_cleaned" / row["doc_id"] / (
        f"{row['doc_id']}_TN-004_soil_classification.md"
    )
    assert copied.read_text(encoding="utf-8") == "A draft note.\n"
    assert report == []


def test_tool_note_reuses_a_staging_id(tmp_path):
    inbox = tmp_path / "01_Source_raw" / "00_Inbox"
    inbox.mkdir(parents=True)
    staging = tmp_path / "05_Metadata" / "_staging"
    staging.mkdir(parents=True)
    (staging / "pending.csv").write_text(
        "doc_id,title,file_name\nKB-020,Soil moisture,TN-4_old.md\n",
        encoding="utf-8",
    )
    (inbox / "TN-004_soil_moisture.md").write_text("note\n", encoding="utf-8")
    rows = scan_knowledge_base(tmp_path, [], extensions=(".md",))
    assert [row["doc_id"] for row in rows] == ["KB-020"]
    assert rows[0]["doc_type"] == "tool_note"
    assert rows[0]["file_name"].startswith("07_Portal_web_content/Tool_notes/KB-020_")


def test_ambiguous_tool_note_stays_in_the_inbox(tmp_path):
    inbox = tmp_path / "01_Source_raw" / "00_Inbox"
    inbox.mkdir(parents=True)
    staging = tmp_path / "05_Metadata" / "_staging"
    staging.mkdir(parents=True)
    (staging / "pending.csv").write_text(
        "doc_id,title,file_name\n"
        "KB-020,First,TN-004_a.md\n"
        "KB-021,Second,TN-4_b.md\n",
        encoding="utf-8",
    )
    (inbox / "TN-004_new.md").write_text("note\n", encoding="utf-8")
    report: list[str] = []
    rows = scan_knowledge_base(tmp_path, [], extensions=(".md",), report=report)
    assert rows == []
    assert (inbox / "TN-004_new.md").is_file()
    assert any("KB-020" in line and "KB-021" in line for line in report)


def test_same_title_does_not_replace_an_existing_file(tmp_path):
    folder = tmp_path / "01_Source_raw" / "02_Reference_and_classification"
    folder.mkdir(parents=True)
    (folder / "KB-006_Soil_Glossary.pdf").write_bytes(b"%PDF-1.4")
    inbox = tmp_path / "01_Source_raw" / "00_Inbox"
    inbox.mkdir(parents=True)
    (inbox / "Soil_Glossary.pdf").write_bytes(b"%PDF-other")
    existing = {
        "doc_id": "KB-006",
        "title": "Soil Glossary",
        "file_name": "02_Reference_and_classification/KB-006_Soil_Glossary.pdf",
        "licence": "unknown - check",
    }
    report: list[str] = []
    rows = scan_knowledge_base(tmp_path, [existing], extensions=(".pdf",), report=report)
    assert len(rows) == 1
    assert rows[0]["file_name"] == existing["file_name"]
    assert (inbox / "Soil_Glossary.pdf").is_file()
    assert any("already has a different file" in line for line in report)


def test_markdown_skips_ocr_and_is_copied(tmp_path):
    raw = tmp_path / "01_Source_raw"
    raw.mkdir()
    (raw / "note.md").write_text("hello\n", encoding="utf-8")
    row = {"doc_id": "KB-001", "licence": "open", "file_name": "note.md"}
    try:
        prepare_row(row, tmp_path, tmp_path / "02_Source_cleaned", force=False)
    except OcrSkipped as exc:
        assert "skipped OCR" in str(exc)
    else:
        raise AssertionError("markdown should not be sent for OCR")
    assert row["pipeline_status"] == "cleaned"
    assert row["ingest_status"] == "cleaned"
    copied = tmp_path / "02_Source_cleaned" / "KB-001" / "note.md"
    assert copied.read_text(encoding="utf-8") == "hello\n"


def test_raw_status_is_kept():
    row = normalise_row({"doc_id": "KB-023", "status": "raw", "doc_type": "database_record"})
    assert row["pipeline_status"] == "raw"
    assert row["doc_type"] == "database_record"
    assert not validate_row(row)


def test_landis_definitive_alias():
    row = normalise_row({"doc_id": "KB-001", "authority_tier": "landis_definitive"})
    assert row["authority_tier"] == "LandIS definitive"


def test_title_rules_pick_the_survey_and_data_folders():
    assert classify_by_title("R117_Soils_in_Devon_IX.pdf") == (
        "01_Survey_reports/Soil_Survey_Records",
        "survey_report",
    )
    assert classify_by_title("Soilscapes_Brochure.pdf") == (
        "05_Data_products_and_policy",
        "data_documentation",
    )
    assert classify_by_title("LandIS_map_NE_Dartmoor_2019.jpg") == (
        "08_Maps_and_figures",
        "map",
    )
    assert classify_by_title("mapping_of_landscapes.pdf") is None
    assert classify_by_title("predicting_bulk_density.pdf") is None


def test_a_file_already_in_a_type_folder_stays_there(tmp_path):
    folder = tmp_path / "01_Source_raw" / "04_Research_papers"
    folder.mkdir(parents=True)
    (folder / "Soil_Glossary.pdf").write_bytes(b"%PDF-1.4")
    rows = scan_knowledge_base(tmp_path, [], extensions=(".pdf",))
    assert rows[0]["file_name"] == "04_Research_papers/Soil_Glossary.pdf"
    assert rows[0]["doc_type"] == "research_paper"
    assert (folder / "Soil_Glossary.pdf").is_file()


def test_doc_type_is_separate_from_document_type():
    row = normalise_row(
        {"doc_id": "KB-001", "doc_type": "reference", "document_type": "brochure"}
    )
    assert row["doc_type"] == "reference"
    assert row["document_type"] == "brochure"
    assert not validate_row(row)


def test_chunking_follows_doc_type_unless_mode_is_set():
    survey = {"doc_type": "survey_report", "chunking_mode": "", "file_name": "01_Survey_reports/a.pdf"}
    assert not uses_structured_chunks(survey)
    assert not skips_prose_chunking(survey)
    mapped = {"doc_type": "map", "chunking_mode": "", "file_name": "08_Maps_and_figures/a.jpg"}
    assert skips_prose_chunking(mapped)
    forced = {"doc_type": "map", "chunking_mode": "prose - section level"}
    assert not skips_prose_chunking(forced)
    glossary = {"doc_type": "reference", "chunking_mode": "structured - one chunk per record"}
    assert uses_structured_chunks(glossary)
    stored = document_record(
        {"doc_id": "KB-003", "licence": "open", "doc_type": "survey_report"},
        "a.pdf",
        "abc",
    )
    assert stored["chunking_mode"] == "prose - section level"


def test_register_round_trip(tmp_path):
    path = tmp_path / "corpus_register.csv"
    save_register(path, worked_example_rows())
    loaded = load_register(path)
    assert loaded[3]["licence"] == "open"
    assert loaded[3]["doc_id"] == "D04"


def test_ocr_keys_and_folder_layout(tmp_path):
    assert snake_case("averagePageConfidenceScore") == "average_page_confidence_score"
    assert snake_case("topLeftX") == "top_left_x"
    payload = normalise_keys(
        {
            "model": "mistral-ocr-test",
            "pages": [
                {
                    "index": 0,
                    "markdown": "Dore Series",
                    "header": "11",
                    "footer": None,
                    "confidenceScores": {"averagePageConfidenceScore": 0.9},
                }
            ],
        }
    )
    dest = write_cleaned_document(tmp_path, "SSR-011", payload)
    page = dest / "pages" / "page-1"
    assert (page / "markdown.md").read_text(encoding="utf-8") == "Dore Series"
    assert "average_page_confidence_score" in (page / "page-metadata.json").read_text(encoding="utf-8")
    (dest / "CURATED").write_text("reviewed\n", encoding="utf-8")
    write_cleaned_document(
        tmp_path,
        "SSR-011",
        {"pages": [{"index": 0, "markdown": "replaced"}]},
    )
    assert (page / "markdown.md").read_text(encoding="utf-8") == "Dore Series"


def test_batch_line_uses_the_doc_id():
    line = batch_line("SSR-011", "https://example.test/file", "document")
    assert line["custom_id"] == "SSR-011"
    assert line["body"]["table_format"] == "html"
    assert line["body"]["include_image_base64"] is False
    custom_id, body = extract_ocr_payload(
        {"custom_id": "SSR-011", "response": {"body": {"pages": []}}}
    )
    assert custom_id == "SSR-011"
    assert body == {"pages": []}
    assert estimate_cost_usd(1000, batch=True) == 2.0
    assert estimate_cost_usd(1000, batch=False) == 4.0


def test_series_chunk_drops_the_running_head_and_keeps_the_table():
    description = "The Dore series is a deep loam over river alluvium. " * 40
    table = "<table><tr><td>6.5</td></tr></table>"
    pages = [
        {
            "index": 29,
            "page_number": 30,
            "header": "SOIL SURVEY RECORD No. 11",
            "footer": "35",
            "markdown": "Dore Series",
            "blocks": [
                {"type": "header", "content": "SOIL SURVEY RECORD No. 11"},
                {"type": "title", "content": "Dore Series"},
                {"type": "text", "content": description},
                {"type": "table", "content": "pH 6.5", "table_id": "tbl-0"},
                {"type": "footer", "content": "See footnote on particle size."},
                {"type": "footer", "content": "35"},
            ],
            "tables": [{"id": "tbl-0", "content": table}],
        }
    ]
    chunks = chunks_from_pages(pages, doc_id="SSR-011", min_words=300, max_words=800)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert "SOIL SURVEY RECORD" not in joined
    assert "35" not in joined
    assert any(chunk.section == "Dore Series" for chunk in chunks)
    tables = [chunk for chunk in chunks if chunk.has_table]
    assert len(tables) == 1
    assert tables[0].table_html == table
    assert "6.5" in tables[0].text
    assert tables[0].entity_codes == ["series:Dore"]
    assert "35" in tables[0].printed_pages
    assert "See footnote" in joined


def test_qa_uses_word_counts_not_the_page_minimum():
    words = [{"word": f"w{i}", "confidence": 0.2} for i in range(6)]
    flagged = review_page(
        {
            "page_number": 4,
            "markdown": "word " * 40,
            "confidence_scores": {
                "minimum_page_confidence_score": 0.1,
                "word_confidence_scores": words,
            },
        }
    )
    assert any("6 words below" in flag for flag in flagged)
    quiet = review_page(
        {
            "page_number": 4,
            "markdown": "word " * 40,
            "confidence_scores": {
                "minimum_page_confidence_score": 0.01,
                "word_confidence_scores": [{"word": "soil", "confidence": 0.95}],
            },
        }
    )
    assert quiet == []
    empty = review_page({"page_number": 2, "markdown": "Plate", "blocks": []})
    assert any("near-empty" in flag for flag in empty)
    year_flags = review_document(
        [{"page_number": 1, "markdown": "Published 1984 " + ("soil " * 20)}],
        register_year="1972",
    )
    assert any("1972" in flag for flag in year_flags)


def test_prefill_does_not_overwrite_a_checked_title():
    pages = [
        {
            "markdown": "# Soils in Herefordshire\n\nRecord No. 11, 1972",
            "blocks": [{"type": "title", "content": "Dore Series"}],
        }
    ]
    row = apply_prefill(
        {"title": "Keep this title", "publication_year": "", "place": "", "entity_codes": ""},
        pages,
    )
    assert row["title"] == "Keep this title"
    assert row["publication_year"] == "1972"
    assert "Herefordshire" in row["place"]
    assert "series:Dore" in row["entity_codes"]
    assert "unverified" in row["metadata_note"]


def test_public_url_stays_hidden_unless_the_licence_is_open():
    open_row = document_record(
        {"doc_id": "D04", "licence": "open", "public_url": "https://example.test/guide"},
        "guide.pdf",
        "abc",
    )
    licensed = document_record(
        {"doc_id": "D03", "licence": "LandIS licence", "public_url": "https://example.test/secret"},
        "bulletin.pdf",
        "abc",
    )
    assert open_row["is_public"] is True
    assert licensed["is_public"] is False


def test_citations_and_suggested_questions():
    sources = [
        SourceRef(1, "a", "D01", "a.pdf", "A", "", 1, 1, "a"),
        SourceRef(2, "b", "D02", "b.pdf", "B", "", 2, 2, "b"),
        SourceRef(3, "c", "D03", "c.pdf", "C", "", 3, 3, "c"),
    ]
    answer, questions = split_suggested_questions(
        "Soilscapes is a national map [1].\n\n<<SUGGESTED>>\n- What is HOST?\n- And classification?\n<<END>>"
    )
    assert "[1]" in answer
    assert "<<" not in answer
    assert questions == ["What is HOST?", "And classification?"]
    kept = sources_cited_in_answer("See [2, 3] only.", sources)
    assert [source.label for source in kept] == [2, 3]


def test_rank_fusion_prefers_a_hit_in_both_lists_and_a_named_series():
    scores = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
    assert scores["b"] > scores["a"]
    boosted = apply_rank_boosts(
        {"a": 1.0, "b": 1.0},
        {
            "a": {"authority_tier": "third-party academic", "entity_codes": "", "superseded": False},
            "b": {"authority_tier": "LandIS definitive", "entity_codes": "series:Dore", "superseded": False},
        },
        "Tell me about the Dore series",
    )
    assert boosted["b"] > boosted["a"]


def test_point_location_and_regulatory_questions_are_detected():
    assert looks_like_point_location("What soil is at SW1A 1AA?")
    assert looks_like_point_location("Grid reference SX 1234 5678")
    assert not looks_like_point_location("What is the Dore series?")
    assert looks_like_regulatory("Do I need planning permission to sample this field?")


def test_collapsed_markdown_table_is_split_onto_lines():
    collapsed = "| Soil | Place | | --- | --- | | Dore | Herefordshire | | Brockhurst | Shropshire |"
    expanded = normalise_markdown_tables(collapsed)
    assert expanded.count("\n") >= 2
    assert "Dore" in expanded


def test_pgvector_value_becomes_a_float_array():
    from pgvector import Vector

    from landis.store_pg import _vector

    values = _vector(Vector([0.25, -0.5, 1.0]))
    assert values.dtype == "float32"
    assert values.shape == (3,)
    assert float(values[0]) == 0.25
