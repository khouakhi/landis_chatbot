"""The Excel register form copies descriptive columns and leaves pipeline columns."""

from __future__ import annotations

from datetime import date

from openpyxl import load_workbook

from landis.register import validate_row
from landis.register_workbook import apply_human_fields, read_workbook_edits, write_workbook


def _row() -> dict[str, str]:
    return {
        "doc_id": "KB-006",
        "title": "Soil Glossary",
        "file_name": "02_Reference_and_classification/KB-006_Soil_Glossary.pdf",
        "document_type": "",
        "place": "",
        "spatial_scale": "",
        "publication_year": "",
        "licence": "LandIS licence",
        "superseded": "unknown - check",
        "authority_tier": "",
        "chunking_mode": "",
        "audience": "",
        "filled_in_by": "",
        "notes": "Title is taken from the file name and is unverified.",
        "subject_primary": "",
        "subject_secondary": "",
        "entity_codes": "",
        "ingest_status": "ingested",
        "sha256": "abc",
        "pipeline_status": "indexed",
        "pages_processed": "7",
    }


def test_workbook_has_dropdowns_colours_and_a_lock(tmp_path):
    path = tmp_path / "corpus_register.xlsx"
    write_workbook(path, [_row()], built_on=date(2026, 11, 1))
    workbook = load_workbook(path)
    sheet = workbook["Corpus register"]
    assert sheet["A3"].value == "Doc ID"
    assert sheet["H3"].value == "Licence"
    assert sheet["U4"].value.startswith('=IF(H4="","Set the licence"')
    assert sheet["A4"].protection.locked is True
    assert sheet["B4"].protection.locked is False
    assert sheet["O4"].protection.locked is False
    assert sheet["R4"].protection.locked is True
    assert sheet.protection.sheet is True
    assert sheet["A4"].font.name == "Arial"
    names = {item.formula1 for item in sheet.data_validations.dataValidation}
    assert "=LicenceList" in names
    assert "=DocumentTypeList" in names
    assert "=SubjectClassList" in names
    assert "Allowed values" in workbook.defined_names["DocumentTypeList"].attr_text
    colour = sheet["B4"].font.color.rgb
    assert colour is not None and colour.endswith("0000FF")
    listed = workbook["Allowed values"]["A2"].value
    assert listed == "survey report"
    help_sheet = workbook["Read me"]
    assert "1 November 2026" in help_sheet["B3"].value
    assert validate_row(_row()) == []


def test_apply_updates_catalogue_fields_and_keeps_pipeline_fields(tmp_path):
    path = tmp_path / "corpus_register.xlsx"
    current = _row()
    write_workbook(path, [current])
    workbook = load_workbook(path)
    sheet = workbook["Corpus register"]
    sheet["B4"] = "Glossary of soil-related terms"
    sheet["D4"] = "reference"
    sheet["G4"] = 2026
    sheet["R4"] = "raw"
    sheet["C4"] = "other.pdf"
    workbook.save(path)

    edited = read_workbook_edits(path)
    assert edited[0]["publication_year"] == "2026"
    assert edited[0]["document_type"] == "reference"
    report = apply_human_fields([current], edited)
    assert report.problems == ()
    assert report.updated == ("KB-006",)
    saved = report.rows[0]
    assert saved["title"] == "Glossary of soil-related terms"
    assert saved["publication_year"] == "2026"
    assert saved["sha256"] == "abc"
    assert saved["pipeline_status"] == "indexed"
    assert saved["ingest_status"] == "ingested"
    assert saved["pages_processed"] == "7"
    assert saved["file_name"].endswith("KB-006_Soil_Glossary.pdf")


def test_apply_rejects_an_unknown_value_and_keeps_the_csv_row():
    current = _row()
    edited = [{"doc_id": "KB-006", "document_type": "survey rpt", "title": "New"}]
    report = apply_human_fields([current], edited)
    assert any("document_type" in problem for problem in report.problems)
    assert report.updated == ()
    assert report.rows[0]["title"] == "Soil Glossary"
    assert report.rows[0]["sha256"] == "abc"


def test_apply_keeps_rows_that_are_in_only_one_file():
    first = _row()
    second = dict(first)
    second["doc_id"] = "KB-007"
    second["title"] = "Policy"
    edited = dict(first)
    edited["title"] = "Glossary of soil-related terms"
    extra = {"doc_id": "KB-999", "title": "Not in the CSV", "licence": "open"}
    report = apply_human_fields([first, second], [edited, extra])
    assert report.problems == ()
    assert report.updated == ("KB-006",)
    assert report.absent_from_workbook == ("KB-007",)
    assert report.absent_from_csv == ("KB-999",)
    assert report.rows[1]["title"] == "Policy"
    assert report.rows[1]["doc_id"] == "KB-007"


def test_unchanged_workbook_reports_no_updates(tmp_path):
    path = tmp_path / "corpus_register.xlsx"
    current = _row()
    write_workbook(path, [current])
    report = apply_human_fields([current], read_workbook_edits(path))
    assert report.problems == ()
    assert report.updated == ()
    assert report.rows[0]["pipeline_status"] == "indexed"
