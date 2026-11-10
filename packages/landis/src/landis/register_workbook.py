"""
Working Excel copy of the corpus register.

The CSV stays the file the commands read and write. This workbook is the
form: dropdowns, colours, and a sheet lock on the columns the scripts own.
Applying it copies only the descriptive columns back into the CSV.

**Created:** 1 November 2026 (UK style).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.worksheet.datavalidation import DataValidation

from .register import (
    AUDIENCES,
    AUTHORITY_TIERS,
    CHUNKING_MODES,
    DOCUMENT_TYPES,
    LICENCES,
    SPATIAL_SCALES,
    SUBJECT_CLASSES,
    SUPERSEDED_VALUES,
    normalise_row,
    validate_row,
)

# Descriptive columns a person fills. Apply copies these and nothing else.
HUMAN_FIELDS: tuple[str, ...] = (
    "title",
    "document_type",
    "place",
    "spatial_scale",
    "publication_year",
    "licence",
    "superseded",
    "authority_tier",
    "chunking_mode",
    "audience",
    "filled_in_by",
    "notes",
    "subject_primary",
    "subject_secondary",
    "entity_codes",
)

# field, heading, kind. kind is edit, later, locked, or check.
_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("doc_id", "Doc ID", "locked"),
    ("title", "Title", "edit"),
    ("file_name", "File name", "locked"),
    ("document_type", "Document type", "edit"),
    ("place", "Place", "edit"),
    ("spatial_scale", "Spatial scale", "edit"),
    ("publication_year", "Publication year", "edit"),
    ("licence", "Licence", "edit"),
    ("superseded", "Superseded", "edit"),
    ("authority_tier", "Authority tier", "edit"),
    ("chunking_mode", "Chunking mode", "edit"),
    ("audience", "Audience", "edit"),
    ("filled_in_by", "Filled in by", "edit"),
    ("notes", "Notes", "edit"),
    ("subject_primary", "Subject class (primary)", "later"),
    ("subject_secondary", "Subject class (secondary)", "later"),
    ("entity_codes", "Entity codes", "later"),
    ("pipeline_status", "Pipeline status", "locked"),
    ("ingest_status", "Ingest status", "locked"),
    ("pages_processed", "Pages", "locked"),
    ("_check", "Check", "check"),
)

_LISTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("DocumentTypeList", "Document type", DOCUMENT_TYPES),
    ("SpatialScaleList", "Spatial scale", SPATIAL_SCALES),
    ("LicenceList", "Licence", LICENCES),
    ("SupersededList", "Superseded", SUPERSEDED_VALUES),
    ("AuthorityTierList", "Authority tier", AUTHORITY_TIERS),
    ("ChunkingModeList", "Chunking mode", CHUNKING_MODES),
    ("AudienceList", "Audience", AUDIENCES),
    ("SubjectClassList", "Subject class", SUBJECT_CLASSES),
)

_DROPDOWN_LIST: dict[str, str] = {
    "document_type": "DocumentTypeList",
    "spatial_scale": "SpatialScaleList",
    "licence": "LicenceList",
    "superseded": "SupersededList",
    "authority_tier": "AuthorityTierList",
    "chunking_mode": "ChunkingModeList",
    "audience": "AudienceList",
    "subject_primary": "SubjectClassList",
    "subject_secondary": "SubjectClassList",
}

_WIDTHS: dict[str, float] = {
    "doc_id": 14,
    "title": 42,
    "file_name": 46,
    "document_type": 20,
    "place": 28,
    "spatial_scale": 16,
    "publication_year": 18,
    "licence": 28,
    "superseded": 18,
    "authority_tier": 24,
    "chunking_mode": 34,
    "audience": 24,
    "filled_in_by": 14,
    "notes": 55,
    "subject_primary": 36,
    "subject_secondary": 36,
    "entity_codes": 18,
    "pipeline_status": 18,
    "ingest_status": 16,
    "pages_processed": 10,
    "_check": 22,
}

_NAVY = "0C406D"
_GREEN = "789C19"
_INK = "1B2430"
_BLUE = "0000FF"
_LINE = "D5DEE8"
_WHITE = "FFFFFF"
_LOCK = "E7EEF5"
_LATER = "F4F6F8"
_AMBER = "FFF4D6"
_PINK = "F8D7DA"
_MINT = "E5F0D0"
_HEADER_LATER = "5C6B7C"

_FONT = "Arial"
_HEADER_ROW = 3
_FIRST_DATA_ROW = 4
_REGISTER_SHEET = "Corpus register"
_LIST_SHEET = "Allowed values"
_HELP_SHEET = "Read me"

_COMMENTS: dict[str, str] = {
    "licence": (
        "open or LandIS licence can be sent for OCR and embedding. "
        "unknown - check and restricted - do not ingest stay on this machine."
    ),
    "superseded": (
        "Age is not superseded. Choose yes only when the text has been replaced. "
        "If you are unsure, choose unknown - check and write the question in Notes."
    ),
    "subject_primary": "Leave blank until the eight subject classes are agreed.",
    "subject_secondary": "Leave blank until the eight subject classes are agreed.",
    "entity_codes": "No agreed list yet. Leave blank.",
    "_check": "Follows the licence cell. Filter this column to see what still needs a decision.",
}


@dataclass(frozen=True)
class ApplyReport:
    """Result of copying descriptive columns onto the CSV rows."""

    rows: list[dict[str, str]]
    updated: tuple[str, ...]
    absent_from_workbook: tuple[str, ...]
    absent_from_csv: tuple[str, ...]
    problems: tuple[str, ...]


def write_workbook(path: Path, rows: list[dict[str, str]], *, built_on: date | None = None) -> None:
    """Write a data-entry workbook. Existing contents of ``path`` are replaced."""
    built_on = built_on or date.today()
    workbook = Workbook()
    register = workbook.active
    register.title = _REGISTER_SHEET
    help_sheet = workbook.create_sheet(_HELP_SHEET)
    lists = workbook.create_sheet(_LIST_SHEET)
    _write_lists(workbook, lists)
    _write_register(register, rows)
    _write_help(help_sheet, built_on=built_on, documents=len(rows))
    register.sheet_view.tabSelected = True
    help_sheet.sheet_view.tabSelected = False
    workbook.active = register
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)


def read_workbook_edits(path: Path) -> list[dict[str, str]]:
    """Read Doc ID and the descriptive columns from a working workbook."""
    workbook = load_workbook(path, data_only=False)
    if _REGISTER_SHEET not in workbook.sheetnames:
        raise ValueError(f"The workbook has no '{_REGISTER_SHEET}' sheet.")
    sheet = workbook[_REGISTER_SHEET]
    headings = {
        _text(sheet.cell(_HEADER_ROW, column).value): column
        for column in range(1, len(_COLUMNS) + 1)
    }
    if headings.get("Doc ID") is None:
        raise ValueError("The register sheet has no Doc ID heading on row 3.")
    wanted = {heading: field for field, heading, kind in _COLUMNS if kind in {"edit", "later"}}
    missing = [heading for heading in wanted if heading not in headings]
    if missing:
        raise ValueError("The register sheet is missing columns: " + ", ".join(missing))
    found: list[dict[str, str]] = []
    blank_run = 0
    for row_index in range(_FIRST_DATA_ROW, sheet.max_row + 1):
        doc_id = _text(sheet.cell(row_index, headings["Doc ID"]).value)
        if not doc_id:
            blank_run += 1
            if blank_run > 5:
                break
            continue
        blank_run = 0
        raw = {"doc_id": doc_id}
        for heading, field in wanted.items():
            raw[field] = _text(sheet.cell(row_index, headings[heading]).value)
        found.append(normalise_row(raw))
    return found


def apply_human_fields(
    csv_rows: list[dict[str, str]],
    workbook_rows: list[dict[str, str]],
) -> ApplyReport:
    """Copy descriptive columns onto matching CSV rows.

    Pipeline columns on the CSV are left as they are. Rows that appear in only
    one of the two files are reported and are not added or removed. Any
    controlled-value problem leaves the CSV rows unchanged.
    """
    originals = [dict(row) for row in csv_rows]
    by_id = {(row.get("doc_id") or "").strip(): row for row in originals}
    seen: set[str] = set()
    problems: list[str] = []
    absent_from_csv: list[str] = []
    for incoming in workbook_rows:
        doc_id = (incoming.get("doc_id") or "").strip()
        if not doc_id:
            continue
        if doc_id in seen:
            problems.append(f"{doc_id}: the workbook lists this Doc ID more than once.")
            continue
        seen.add(doc_id)
        current = by_id.get(doc_id)
        if current is None:
            absent_from_csv.append(doc_id)
            continue
        for field in HUMAN_FIELDS:
            current[field] = incoming.get(field, "")
        problems.extend(validate_row(current))
    if problems:
        return ApplyReport(
            rows=[dict(row) for row in csv_rows],
            updated=(),
            absent_from_workbook=(),
            absent_from_csv=tuple(absent_from_csv),
            problems=tuple(problems),
        )
    updated: list[str] = []
    for doc_id, current in by_id.items():
        if doc_id not in seen:
            continue
        previous = next(row for row in csv_rows if (row.get("doc_id") or "").strip() == doc_id)
        if any((previous.get(field) or "") != (current.get(field) or "") for field in HUMAN_FIELDS):
            updated.append(doc_id)
    absent_from_workbook = tuple(
        doc_id for doc_id in by_id if doc_id and doc_id not in seen
    )
    return ApplyReport(
        rows=originals,
        updated=tuple(updated),
        absent_from_workbook=absent_from_workbook,
        absent_from_csv=tuple(absent_from_csv),
        problems=(),
    )


def _write_lists(workbook: Workbook, sheet) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.tabColor = _HEADER_LATER
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.oddFooter.right.text = "Page &P of &N"
    header_font = Font(name=_FONT, bold=True, color=_WHITE, size=10)
    header_fill = PatternFill("solid", fgColor=_NAVY)
    value_font = Font(name=_FONT, color=_INK, size=10)
    for index, (range_name, heading, values) in enumerate(_LISTS, start=1):
        header = sheet.cell(1, index, heading)
        header.font = header_font
        header.fill = header_fill
        header.alignment = Alignment(vertical="center", wrap_text=True)
        for row_index, value in enumerate(values, start=2):
            cell = sheet.cell(row_index, index, value)
            cell.font = value_font
            cell.alignment = Alignment(vertical="center")
        last = 1 + len(values)
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = 42
        workbook.defined_names.add(
            DefinedName(
                name=range_name,
                attr_text=f"'{_LIST_SHEET}'!${letter}$2:${letter}${last}",
            )
        )
    sheet.row_dimensions[1].height = 22
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(_LISTS))}1"
    sheet.freeze_panes = "A2"
    sheet.oddHeader.left.text = "LandIS corpus register — allowed values"
    note = sheet.cell(
        12,
        1,
        "These lists are the dropdowns on Corpus register. "
        "A new spelling belongs in the list only after the team has agreed it.",
    )
    note.font = Font(name=_FONT, italic=True, color=_HEADER_LATER, size=10)
    sheet.merge_cells(start_row=12, start_column=1, end_row=12, end_column=4)


def _write_register(sheet, rows: list[dict[str, str]]) -> None:
    last_column = get_column_letter(len(_COLUMNS))
    last_row = _HEADER_ROW + max(len(rows), 1)
    sheet.sheet_view.showGridLines = False
    sheet.sheet_view.zoomScale = 110
    sheet.sheet_properties.tabColor = _NAVY
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.horizontalCentered = True
    sheet.print_title_rows = "1:3"
    sheet.page_margins.left = 0.4
    sheet.page_margins.right = 0.4
    sheet.page_margins.top = 0.6
    sheet.page_margins.bottom = 0.5
    sheet.oddHeader.left.text = "LandIS corpus register"
    sheet.oddFooter.left.text = "Save, then run landis-register apply from the code folder"
    sheet.oddFooter.right.text = "Page &P of &N"
    banner = sheet.cell(1, 1, "LandIS corpus register")
    banner.font = Font(name=_FONT, bold=True, color=_WHITE, size=16)
    banner.fill = PatternFill("solid", fgColor=_NAVY)
    banner.alignment = Alignment(vertical="center", indent=1)
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(_COLUMNS))
    sheet.row_dimensions[1].height = 28
    for column in range(1, len(_COLUMNS) + 1):
        sheet.cell(1, column).fill = PatternFill("solid", fgColor=_NAVY)

    reminder = sheet.cell(
        2,
        1,
        "Edit the blue text.\nGrey cells stay blank.\nBlue-grey cells: commands.",
    )
    reminder.font = Font(name=_FONT, bold=True, color=_NAVY, size=10)
    reminder.alignment = Alignment(vertical="center", wrap_text=True, indent=1)
    reminder.fill = PatternFill("solid", fgColor="F7F9FB")
    sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=2)
    sheet.cell(2, 2).fill = PatternFill("solid", fgColor="F7F9FB")
    instruction = sheet.cell(
        2,
        3,
        "Dropdowns are on Document type, Spatial scale, Licence, Superseded, Authority tier, "
        "Chunking mode, Audience, and the subject classes. Save this file, then from the code "
        "folder run uv run landis-register apply. Your columns are copied into the CSV. "
        "The file hash, status, and page count stay as the CSV has them.",
    )
    instruction.font = Font(name=_FONT, color=_INK, size=10)
    instruction.alignment = Alignment(vertical="center", wrap_text=True, indent=1)
    instruction.fill = PatternFill("solid", fgColor="F7F9FB")
    sheet.merge_cells(start_row=2, start_column=3, end_row=2, end_column=len(_COLUMNS))
    for column in range(3, len(_COLUMNS) + 1):
        sheet.cell(2, column).fill = PatternFill("solid", fgColor="F7F9FB")
    sheet.row_dimensions[2].height = 48

    header_alignment = Alignment(vertical="center", wrap_text=True)
    thin = Border(
        left=Side(style="thin", color=_LINE),
        right=Side(style="thin", color=_LINE),
        top=Side(style="thin", color=_LINE),
        bottom=Side(style="thin", color=_LINE),
    )
    for index, (field, heading, kind) in enumerate(_COLUMNS, start=1):
        cell = sheet.cell(_HEADER_ROW, index, heading)
        cell.font = Font(name=_FONT, bold=True, color=_header_font(kind), size=10)
        cell.fill = PatternFill("solid", fgColor=_header_colour(kind))
        cell.alignment = header_alignment
        cell.border = Border(bottom=Side(style="medium", color=_NAVY))
        cell.protection = Protection(locked=True)
        letter = get_column_letter(index)
        sheet.column_dimensions[letter].width = _WIDTHS[field]
        note = _COMMENTS.get(field)
        if note:
            cell.comment = Comment(note, "LandIS", width=260, height=70)
    sheet.row_dimensions[_HEADER_ROW].height = 32
    sheet.freeze_panes = "C4"
    sheet.auto_filter.ref = f"A{_HEADER_ROW}:{last_column}{last_row}"

    licence_letter = _letter("licence")
    check_letter = _letter("_check")
    for offset, row in enumerate(rows):
        row_index = _FIRST_DATA_ROW + offset
        sheet.row_dimensions[row_index].height = 32
        for index, (field, _heading, kind) in enumerate(_COLUMNS, start=1):
            cell = sheet.cell(row_index, index)
            cell.border = thin
            cell.alignment = Alignment(
                vertical="center",
                wrap_text=field in {"notes", "title", "file_name"},
            )
            if kind == "check":
                cell.value = _check_formula(licence_letter, row_index)
                cell.font = Font(name=_FONT, color=_INK, size=10)
                cell.fill = PatternFill("solid", fgColor=_WHITE)
                cell.protection = Protection(locked=True)
                continue
            value = row.get(field, "")
            cell.value = value if value else None
            if field == "publication_year":
                cell.number_format = "@"
                cell.value = value or None
            if kind == "edit":
                cell.font = Font(name=_FONT, color=_BLUE, size=10)
                cell.fill = PatternFill("solid", fgColor=_WHITE)
                cell.protection = Protection(locked=False)
            elif kind == "later":
                cell.font = Font(name=_FONT, color=_BLUE, size=10)
                cell.fill = PatternFill("solid", fgColor=_LATER)
                cell.protection = Protection(locked=False)
            else:
                cell.font = Font(name=_FONT, color=_INK, size=10)
                if field == "doc_id":
                    cell.font = Font(name=_FONT, bold=True, color=_NAVY, size=10)
                cell.fill = PatternFill("solid", fgColor=_LOCK)
                cell.protection = Protection(locked=True)
        _group_border(sheet, row_index, "subject_primary")
        _group_border(sheet, row_index, "pipeline_status")

    _add_dropdowns(sheet, last_row)
    _add_status_colours(sheet, licence_letter, check_letter, last_row)
    if rows:
        sheet.sheet_view.selection[0].activeCell = "B4"
        sheet.sheet_view.selection[0].sqref = "B4"

    protection = sheet.protection
    protection.sheet = True
    protection.autoFilter = False
    protection.sort = False
    protection.formatColumns = False
    protection.formatRows = False
    protection.formatCells = True
    protection.insertColumns = True
    protection.insertRows = True
    protection.deleteColumns = True
    protection.deleteRows = True
    protection.insertHyperlinks = True
    protection.selectLockedCells = False
    protection.selectUnlockedCells = False
    protection.objects = True
    protection.scenarios = True
    protection.pivotTables = True


def _write_help(sheet, *, built_on: date, documents: int) -> None:
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.tabColor = _GREEN
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.column_dimensions["A"].width = 3
    sheet.column_dimensions["B"].width = 28
    sheet.column_dimensions["C"].width = 92
    sheet.oddFooter.right.text = "Page &P of &N"
    sheet.row_dimensions[1].height = 10

    title = sheet.cell(2, 2, "How to fill in the corpus register")
    title.font = Font(name=_FONT, bold=True, color=_WHITE, size=16)
    title.fill = PatternFill("solid", fgColor=_NAVY)
    title.alignment = Alignment(vertical="center", indent=1)
    sheet.merge_cells("B2:C2")
    sheet.cell(2, 3).fill = PatternFill("solid", fgColor=_NAVY)
    sheet.row_dimensions[2].height = 32

    stamp = sheet.cell(
        3,
        2,
        f"Working copy built {_uk_date(built_on)} from corpus_register.csv. "
        f"{documents} document{'s' if documents != 1 else ''}. "
        "The CSV remains the file the commands use.",
    )
    stamp.font = Font(name=_FONT, italic=True, color=_HEADER_LATER, size=10)
    stamp.alignment = Alignment(wrap_text=True, vertical="center")
    sheet.merge_cells("B3:C3")
    sheet.row_dimensions[3].height = 32

    sections = [
        (
            "What you edit",
            "Blue text on a white cell. Document type, spatial scale, licence, superseded, "
            "authority tier, chunking mode, and audience have dropdowns. Place, publication year, "
            "your initials, and notes are typed. Pick a listed value. A new spelling will be refused "
            "when the workbook is applied.",
        ),
        (
            "Leave blank for now",
            "Subject class (primary), subject class (secondary), and entity codes are shaded grey. "
            "The subject dropdown is there so a later pass cannot invent a class. Leave the three "
            "columns empty until the eight classes are agreed.",
        ),
        (
            "Filled by the commands",
            "Doc ID, file name, pipeline status, ingest status, and pages are shaded blue-grey and "
            "locked. Scan, OCR, and embed write those values into the CSV, together with the file "
            "hash, which is kept off this sheet.",
        ),
        (
            "Check column",
            "Set the licence — the licence cell is empty. Confirm the licence — the value is "
            "unknown - check. Held back — restricted, do not ingest. Licence set — open or "
            "LandIS licence, so the other white cells can be filled from the document.",
        ),
        (
            "Licence",
            "Only open and LandIS licence may be sent for OCR and embedding. unknown - check and "
            "restricted - do not ingest stay on this machine. If a document cannot be ingested, "
            "set the licence and leave the rest until that is resolved.",
        ),
        (
            "Age is not superseded",
            "A 1984 bulletin can still be current. Publication year dates the citation. Superseded "
            "means the text has been replaced. If you are unsure, choose unknown - check and write "
            "the question in Notes.",
        ),
        (
            "Save your edits",
            "Save this workbook. In the code folder run: uv run landis-register apply. "
            "That copies the white and grey descriptive columns into corpus_register.csv and leaves "
            "the file hash, pipeline status, and page count as the CSV has them. A copy of the CSV "
            "is kept in 05_Metadata/_backup first. Sorting or filtering here does not reorder the CSV.",
        ),
        (
            "Refresh this file",
            "After a scan or an OCR run, close this workbook and run: uv run landis-register workbook. "
            "That rebuilds it from the CSV. Apply any edits first, or the rebuild will replace them. "
            "The sheet is protected without a password. To unlock it: Review, Unprotect Sheet.",
        ),
    ]
    row_index = 5
    for heading, body in sections:
        label = sheet.cell(row_index, 2, heading)
        label.font = Font(name=_FONT, bold=True, color=_WHITE, size=11)
        label.fill = PatternFill("solid", fgColor=_NAVY)
        label.alignment = Alignment(vertical="center", wrap_text=True, indent=1)
        text = sheet.cell(row_index, 3, body)
        text.font = Font(name=_FONT, color=_INK, size=11)
        text.alignment = Alignment(vertical="center", wrap_text=True)
        sheet.row_dimensions[row_index].height = 72
        row_index += 1

    sheet.row_dimensions[4].height = 8
    sheet.print_title_rows = "2:3"
    sheet.oddHeader.left.text = "LandIS corpus register"


def _add_dropdowns(sheet, last_row: int) -> None:
    prompts = {
        "licence": "Only open and LandIS licence can be sent for OCR.",
        "subject_primary": "Leave blank until the classes are agreed.",
        "subject_secondary": "Leave blank until the classes are agreed.",
    }
    for field, range_name in _DROPDOWN_LIST.items():
        letter = _letter(field)
        validation = DataValidation(
            type="list",
            formula1=f"={range_name}",
            allow_blank=True,
        )
        validation.errorTitle = "Not an allowed value"
        validation.error = "Choose a value from the list, or leave the cell blank."
        validation.errorStyle = "stop"
        validation.showErrorMessage = True
        validation.showInputMessage = field in prompts
        if field in prompts:
            validation.promptTitle = _heading(field)
            validation.prompt = prompts[field]
        validation.add(f"{letter}{_FIRST_DATA_ROW}:{letter}{last_row}")
        sheet.add_data_validation(validation)


def _add_status_colours(sheet, licence_letter: str, check_letter: str, last_row: int) -> None:
    amber = PatternFill("solid", fgColor=_AMBER)
    pink = PatternFill("solid", fgColor=_PINK)
    mint = PatternFill("solid", fgColor=_MINT)
    licence_range = f"{licence_letter}{_FIRST_DATA_ROW}:{licence_letter}{last_row}"
    check_range = f"{check_letter}{_FIRST_DATA_ROW}:{check_letter}{last_row}"
    sheet.conditional_formatting.add(
        licence_range,
        FormulaRule(formula=[f'{licence_letter}{_FIRST_DATA_ROW}=""'], fill=amber),
    )
    sheet.conditional_formatting.add(
        licence_range,
        CellIsRule(operator="equal", formula=['"unknown - check"'], fill=amber),
    )
    sheet.conditional_formatting.add(
        licence_range,
        CellIsRule(operator="equal", formula=['"restricted - do not ingest"'], fill=pink),
    )
    sheet.conditional_formatting.add(
        licence_range,
        CellIsRule(operator="equal", formula=['"open"'], fill=mint),
    )
    sheet.conditional_formatting.add(
        licence_range,
        CellIsRule(operator="equal", formula=['"LandIS licence"'], fill=mint),
    )
    for text, fill in (
        ("Set the licence", amber),
        ("Confirm the licence", amber),
        ("Held back", pink),
        ("Licence set", mint),
    ):
        sheet.conditional_formatting.add(
            check_range,
            CellIsRule(operator="equal", formula=[f'"{text}"'], fill=fill),
        )


def _check_formula(licence_letter: str, row_index: int) -> str:
    cell = f"{licence_letter}{row_index}"
    return (
        f'=IF({cell}="","Set the licence",'
        f'IF({cell}="unknown - check","Confirm the licence",'
        f'IF({cell}="restricted - do not ingest","Held back","Licence set")))'
    )


def _group_border(sheet, row_index: int, field: str) -> None:
    cell = sheet.cell(row_index, _index(field))
    cell.border = Border(
        left=Side(style="medium", color=_NAVY),
        right=Side(style="thin", color=_LINE),
        top=Side(style="thin", color=_LINE),
        bottom=Side(style="thin", color=_LINE),
    )


def _header_colour(kind: str) -> str:
    if kind == "later":
        return _LATER
    if kind == "check":
        return _GREEN
    if kind == "locked":
        return _LOCK
    return _NAVY


def _header_font(kind: str) -> str:
    if kind in {"edit", "check"}:
        return _WHITE
    return _NAVY


def _letter(field: str) -> str:
    return get_column_letter(_index(field))


def _index(field: str) -> int:
    for index, (name, _heading, _kind) in enumerate(_COLUMNS, start=1):
        if name == field:
            return index
    raise KeyError(field)


def _heading(field: str) -> str:
    for name, heading, _kind in _COLUMNS:
        if name == field:
            return heading
    raise KeyError(field)


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, datetime):
        return str(value.year) if value.month == 1 and value.day == 1 else value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    return str(value).strip()


def _uk_date(value: date) -> str:
    months = (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    )
    return f"{value.day} {months[value.month - 1]} {value.year}"
