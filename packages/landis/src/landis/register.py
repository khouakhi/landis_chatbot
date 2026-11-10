"""
Corpus register: one row per source document.

Columns follow the LandIS corpus register workbook (v0.1, October 2026).
Pipeline columns (sha256, pipeline_status, qa_flags) are filled by scripts.
Subject classes are left blank until the ontology is agreed.

**Created:** 29-10-2026 (UK style).
"""

from __future__ import annotations

import csv
import hashlib
import re
from pathlib import Path

from .ocr_layout import copy_plain_text

FIELDNAMES: tuple[str, ...] = (
    "doc_id",
    "title",
    "file_name",
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
    "ingest_status",
    "sha256",
    "public_url",
    "pipeline_status",
    "qa_flags",
    "pages_processed",
    "ocr_model",
    "metadata_note",
    "doc_type",
    "source_url",
    "downloaded_date",
)

HEADER_ALIASES: dict[str, str] = {
    "doc id": "doc_id",
    "source_doc_id": "doc_id",
    "file name or location": "file_name",
    "file_path": "file_name",
    "document type": "document_type",
    "spatial scale": "spatial_scale",
    "publication year": "publication_year",
    "authority tier": "authority_tier",
    "chunking mode": "chunking_mode",
    "filled in by": "filled_in_by",
    "notes / queries": "notes",
    "subject class (primary)": "subject_primary",
    "subject class (secondary)": "subject_secondary",
    "entity codes": "entity_codes",
    "ingest status": "ingest_status",
    "status": "pipeline_status",
}

DOCUMENT_TYPES: tuple[str, ...] = (
    "survey report",
    "monograph",
    "journal paper",
    "brochure",
    "policy",
    "web page",
    "database record",
    "reference",
    "map legend",
)
# Folder vocabulary under 01_Source_raw. Separate from document_type.
DOC_TYPES: tuple[str, ...] = (
    "survey_report",
    "reference",
    "technical_report",
    "research_paper",
    "data_documentation",
    "guidance",
    "web_page",
    "tool_note",
    "map",
    "external_policy",
    "database_record",
)
# First folder under 01_Source_raw. 00_Inbox stays blank until a file is triaged.
FOLDER_DOC_TYPES: dict[str, str] = {
    "01_Survey_reports": "survey_report",
    "02_Reference_and_classification": "reference",
    "03_Technical_reports_and_monographs": "technical_report",
    "04_Research_papers": "research_paper",
    "05_Data_products_and_policy": "data_documentation",
    "06_Guidance_and_brochures": "guidance",
    "07_Portal_web_content": "web_page",
    "08_Maps_and_figures": "map",
    "09_External_policy": "external_policy",
}
LICENCES: tuple[str, ...] = (
    "open",
    "LandIS licence",
    "restricted - do not ingest",
    "unknown - check",
)
SUPERSEDED_VALUES: tuple[str, ...] = ("no", "yes", "unknown - check")
AUTHORITY_TIERS: tuple[str, ...] = (
    "LandIS definitive",
    "UK government",
    "third-party academic",
    "other",
)
CHUNKING_MODES: tuple[str, ...] = (
    "prose - section level",
    "structured - one chunk per record",
    "mixed - needs discussion",
)
AUDIENCES: tuple[str, ...] = (
    "general public",
    "land manager / farmer",
    "planner",
    "researcher",
    "all",
)
SPATIAL_SCALES: tuple[str, ...] = (
    "national",
    "regional",
    "1:250,000",
    "1:25,000",
    "site",
)
# October 2026 workbook. Leave live rows blank until the team ratifies the list.
SUBJECT_CLASSES: tuple[str, ...] = (
    "1 Soil mapping units",
    "2 Classification and terminology",
    "3 Soil properties",
    "4 Landscape and environmental context",
    "5 Applications and interpretations",
    "6 Data products and access",
    "7 Evidence base and provenance",
    "8 Service scope and limits",
)
INGEST_STATUSES: tuple[str, ...] = (
    "not started",
    "cleaned",
    "labelled",
    "ingested",
    "excluded",
)
PIPELINE_STATUSES: tuple[str, ...] = (
    "raw",
    "not_started",
    "ocr_queued",
    "ocr_done",
    "needs_review",
    "cleaned",
    "indexed",
    "excluded",
)

# External processing is allowed only for these licence values.
OCR_LICENCES: frozenset[str] = frozenset({"open", "LandIS licence"})

_CONTROLLED: dict[str, tuple[str, ...]] = {
    "document_type": DOCUMENT_TYPES,
    "doc_type": DOC_TYPES,
    "licence": LICENCES,
    "superseded": SUPERSEDED_VALUES,
    "authority_tier": AUTHORITY_TIERS,
    "chunking_mode": CHUNKING_MODES,
    "audience": AUDIENCES,
    "spatial_scale": SPATIAL_SCALES,
    "subject_primary": SUBJECT_CLASSES,
    "subject_secondary": SUBJECT_CLASSES,
    "ingest_status": INGEST_STATUSES,
    "pipeline_status": PIPELINE_STATUSES,
}

_PIPELINE_ALIASES: dict[str, str] = {
    "not started": "not_started",
    "ocr done": "ocr_done",
    "needs review": "needs_review",
}


def empty_row() -> dict[str, str]:
    return {name: "" for name in FIELDNAMES}


def _canonical(value: str, allowed: tuple[str, ...]) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    for item in allowed:
        if text.lower() == item.lower():
            return item
    return text


def normalise_header(name: str) -> str:
    key = (name or "").strip().lower()
    return HEADER_ALIASES.get(key, key.replace(" ", "_"))


def normalise_row(raw: dict[str, str]) -> dict[str, str]:
    """Map aliases onto the canonical columns and tidy controlled values."""
    row = empty_row()
    extras: dict[str, str] = {}
    for key, value in raw.items():
        column = normalise_header(str(key))
        text = "" if value is None else str(value).strip()
        if column in row:
            row[column] = text
        elif column:
            extras[column] = text
    if row["authority_tier"].lower() == "landis_definitive":
        row["authority_tier"] = "LandIS definitive"
    for column, allowed in _CONTROLLED.items():
        row[column] = _canonical(row[column], allowed)
    pipeline = row["pipeline_status"].lower().replace("-", "_")
    pipeline = _PIPELINE_ALIASES.get(pipeline, pipeline)
    if pipeline in PIPELINE_STATUSES:
        row["pipeline_status"] = pipeline
    elif not row["pipeline_status"]:
        row["pipeline_status"] = "not_started"
    if not row["ingest_status"]:
        row["ingest_status"] = "not started"
    row.update(extras)
    return row


def validate_row(row: dict[str, str]) -> list[str]:
    """Return human-readable problems. Blank controlled fields are allowed."""
    problems: list[str] = []
    doc_id = (row.get("doc_id") or "").strip()
    if not doc_id:
        return ["A row is missing a doc id."]
    for column, allowed in _CONTROLLED.items():
        value = (row.get(column) or "").strip()
        if value and value not in allowed:
            problems.append(
                f"{doc_id}: {column} {value!r} is not an allowed value."
            )
    year = (row.get("publication_year") or "").strip()
    if year and not re.fullmatch(r"\d{4}", year):
        problems.append(f"{doc_id}: publication year {year!r} should be a four-digit year.")
    return problems


def licence_allows_external_processing(licence: str) -> bool:
    """True only for open and LandIS-licence documents."""
    return (licence or "").strip() in OCR_LICENCES


def load_register(path: Path) -> list[dict[str, str]]:
    """Load a CSV register. Missing files yield an empty list."""
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = []
        for raw in reader:
            row = normalise_row(raw)
            if (row.get("doc_id") or "").strip():
                rows.append(row)
    return rows


def save_register(path: Path, rows: list[dict[str, str]]) -> None:
    """Write the register. Existing extra columns are kept."""
    path.parent.mkdir(parents=True, exist_ok=True)
    extras: list[str] = []
    seen = set(FIELDNAMES)
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                extras.append(key)
    fieldnames = list(FIELDNAMES) + extras
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def select_rows(
    rows: list[dict[str, str]],
    *,
    pipeline_status: str | None = None,
    doc_id: str | None = None,
) -> list[dict[str, str]]:
    chosen = rows
    if pipeline_status:
        chosen = [row for row in chosen if row.get("pipeline_status") == pipeline_status]
    if doc_id:
        chosen = [row for row in chosen if row.get("doc_id") == doc_id]
    return chosen


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_source_file(knowledge_base: Path, file_name: str) -> Path | None:
    """Find a register file under the raw folder or the knowledge-base root."""
    name = (file_name or "").strip()
    if not name or name.upper().startswith("TO BE"):
        return None
    candidate = Path(name)
    options = []
    if candidate.is_absolute():
        options.append(candidate)
    else:
        options.extend(
            [
                knowledge_base / "01_Source_raw" / candidate,
                knowledge_base / candidate,
            ]
        )
    for option in options:
        if option.is_file():
            return option
    return None


def title_from_filename(file_name: str) -> str:
    stem = Path(file_name).stem.replace("_", " ").replace("-", " ")
    return re.sub(r"\s+", " ", stem).strip()


# Title words that name a document type. Topic words are not used.
# The first match wins, so a more specific phrase stays above a general one.
_TITLE_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\b(?:soilscapes|natmap)\b", re.IGNORECASE),
        "05_Data_products_and_policy",
        "data_documentation",
    ),
    (
        re.compile(r"\bdata (?:policy|structure|structures|product|products)\b", re.IGNORECASE),
        "05_Data_products_and_policy",
        "data_documentation",
    ),
    (
        re.compile(r"\binformation and data\b", re.IGNORECASE),
        "05_Data_products_and_policy",
        "data_documentation",
    ),
    (
        re.compile(r"\bdatabase\b", re.IGNORECASE),
        "05_Data_products_and_policy",
        "data_documentation",
    ),
    (
        re.compile(r"\bbulletins?\b", re.IGNORECASE),
        "01_Survey_reports/Soil_Survey_Bulletins",
        "survey_report",
    ),
    (
        re.compile(r"\bmemoirs?\b", re.IGNORECASE),
        "01_Survey_reports/Soil_Memoirs",
        "survey_report",
    ),
    (
        re.compile(r"\bspecial surveys?\b", re.IGNORECASE),
        "01_Survey_reports/Special_Surveys",
        "survey_report",
    ),
    (
        re.compile(r"\b(?:soil survey records?|soils in|SSR)\b|\bR\d{2,4}\b", re.IGNORECASE),
        "01_Survey_reports/Soil_Survey_Records",
        "survey_report",
    ),
    (
        re.compile(r"\b(?:glossary|bibliography|classification)\b", re.IGNORECASE),
        "02_Reference_and_classification",
        "reference",
    ),
    (
        re.compile(r"\b(?:defra|environment agency)\b", re.IGNORECASE),
        "09_External_policy",
        "external_policy",
    ),
    (
        re.compile(r"\b(?:brochures?|leaflets?|guidance|user guide)\b", re.IGNORECASE),
        "06_Guidance_and_brochures",
        "guidance",
    ),
    (
        re.compile(
            r"\b(?:technical reports?|monographs?|host|sslrc)\b|\bhydrology of soil types\b",
            re.IGNORECASE,
        ),
        "03_Technical_reports_and_monographs",
        "technical_report",
    ),
    (
        re.compile(r"\b(?:journals?|proceedings|conference papers?|doi)\b", re.IGNORECASE),
        "04_Research_papers",
        "research_paper",
    ),
    (
        re.compile(r"\bsoil surveys?\b", re.IGNORECASE),
        "01_Survey_reports/Soil_Survey_Records",
        "survey_report",
    ),
)
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".tif", ".tiff", ".webp"})


def classify_by_title(file_name: str) -> tuple[str, str] | None:
    """Return (folder under 01_Source_raw, doc_type) when the filename matches one type.

    Returns None when nothing matches. The caller leaves that file in the inbox.
    """
    stem = title_from_filename(file_name)
    suffix = Path(file_name).suffix.lower()
    if suffix in {".html", ".htm"} or re.search(r"\bweb ?pages?\b", stem, re.IGNORECASE):
        return "07_Portal_web_content", "web_page"
    for pattern, folder, doc_type in _TITLE_RULES:
        if pattern.search(stem):
            return folder, doc_type
    if suffix in _IMAGE_SUFFIXES or re.search(r"\bmaps?\b", stem, re.IGNORECASE):
        return "08_Maps_and_figures", "map"
    return None


TOOL_NOTE_FOLDER = "07_Portal_web_content/Tool_notes"
TOOL_NOTE_QA = "draft, needs LandIS sign-off"
_TN_NUMBER = re.compile(r"TN-(\d+)", re.IGNORECASE)


def doc_type_from_relative(file_name: str) -> str:
    """Document type from the folder under 01_Source_raw. Inbox stays blank."""
    parts = Path((file_name or "").replace("\\", "/")).parts
    if not parts:
        return ""
    if parts[0] == "07_Portal_web_content" and len(parts) > 1 and parts[1] == "Tool_notes":
        return "tool_note"
    return FOLDER_DOC_TYPES.get(parts[0], "")


def doc_type_for_row(row: dict[str, str]) -> str:
    """Prefer the register column. Fall back to the file's type folder."""
    explicit = (row.get("doc_type") or "").strip()
    if explicit:
        return explicit
    return doc_type_from_relative(row.get("file_name") or "")


def next_kb_id(rows: list[dict[str, str]]) -> str:
    highest = 0
    for row in rows:
        match = re.fullmatch(r"KB-(\d+)", (row.get("doc_id") or "").strip())
        if match:
            highest = max(highest, int(match.group(1)))
    return f"KB-{highest + 1:03d}"


def _iter_source_files(
    knowledge_base: Path, extensions: tuple[str, ...]
) -> list[tuple[Path, str]]:
    """Files at the knowledge-base root, then every file under 01_Source_raw."""
    found: list[tuple[Path, str]] = []
    raw = knowledge_base / "01_Source_raw"
    if raw.is_dir():
        for path in sorted(raw.rglob("*")):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in extensions:
                continue
            relative = path.relative_to(raw).as_posix()
            found.append((path, relative))
    if knowledge_base.is_dir():
        for path in sorted(knowledge_base.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in extensions:
                continue
            found.append((path, path.name))
    return found


def _awaiting_folder(knowledge_base: Path, path: Path, relative: str) -> bool:
    """True for the inbox and for a file sitting directly in 01_Source_raw."""
    raw = knowledge_base / "01_Source_raw"
    try:
        path.resolve().relative_to(raw.resolve())
    except ValueError:
        return False
    parts = Path(relative).parts
    return len(parts) == 1 or parts[0] == "00_Inbox"


def is_inbox_tool_note(relative: str, file_name: str) -> bool:
    """True for an inbox file named TN-*.md."""
    parts = Path(relative.replace("\\", "/")).parts
    if not parts or parts[0] != "00_Inbox":
        return False
    return re.match(r"TN-.*\.md$", Path(file_name).name, re.IGNORECASE) is not None


def tn_number(text: str) -> str:
    """TN number with leading zeros removed, so TN-004 and TN-4 are the same."""
    match = _TN_NUMBER.search(text or "")
    if not match:
        return ""
    return str(int(match.group(1)))


def _title_key(text: str) -> str:
    stem = Path((text or "").replace("\\", "/")).name
    stem = Path(stem).stem.replace("_", " ").replace("-", " ")
    stem = re.sub(r"\s+", " ", stem).strip().lower()
    return re.sub(r"^kb \d+\s+", "", stem)


def matching_doc_ids(file_name: str, candidates: list[dict[str, str]]) -> list[str]:
    """Doc ids that share this file's title or TN number. Order follows the candidates."""
    number = tn_number(file_name)
    title = _title_key(file_name)
    found: list[str] = []
    for row in candidates:
        doc_id = (row.get("doc_id") or "").strip()
        if not doc_id or doc_id in found:
            continue
        row_number = tn_number(row.get("file_name", "")) or tn_number(row.get("title", ""))
        row_title = _title_key(row.get("title", "")) or _title_key(row.get("file_name", ""))
        same_number = bool(number) and number == row_number
        same_title = bool(title) and title == row_title
        if same_number or same_title:
            found.append(doc_id)
    return found


def load_staging_rows(knowledge_base: Path) -> list[dict[str, str]]:
    """Rows in 05_Metadata/_staging/*.csv. Missing folder yields an empty list."""
    staging = knowledge_base / "05_Metadata" / "_staging"
    if not staging.is_dir():
        return []
    found: list[dict[str, str]] = []
    for path in sorted(staging.glob("*.csv")):
        found.extend(load_register(path))
    return found


def _mark_tool_note(row: dict[str, str], *, fresh: bool) -> None:
    """Record a tool note. An existing reviewed row keeps its authority and flags."""
    row["doc_type"] = "tool_note"
    if fresh or not (row.get("authority_tier") or "").strip():
        row["authority_tier"] = "LandIS definitive"
    if fresh or (row.get("pipeline_status") or "") in {"", "not_started"}:
        row["pipeline_status"] = "needs_review"
    if fresh or not (row.get("qa_flags") or "").strip():
        row["qa_flags"] = TOOL_NOTE_QA


def _place_by_title(
    knowledge_base: Path,
    path: Path,
    relative: str,
    doc_id: str,
    decision: tuple[str, str] | None = None,
) -> tuple[Path, str, str, str]:
    """Move an inbox file when its filename matches one document type.

    Returns the path, the register path, doc_type, and a short placement note.
    A file already in a type folder is left where it is. An unmatched title
    stays in the inbox.
    """
    if not _awaiting_folder(knowledge_base, path, relative):
        return path, relative, doc_type_from_relative(relative), ""
    if decision is None:
        decision = classify_by_title(path.name)
    if decision is None:
        return path, relative, "", "left unsorted because the title did not match a document type"
    folder, doc_type = decision
    placed = "as a tool note" if doc_type == "tool_note" else "from the title"
    new_name = path.name if path.name.startswith(f"{doc_id}_") else f"{doc_id}_{path.name}"
    dest = knowledge_base / "01_Source_raw" / folder / new_name
    if dest.exists():
        return (
            path,
            relative,
            "",
            f"left unsorted because {new_name} is already in {folder}",
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    path.rename(dest)
    return dest, f"{folder}/{new_name}", doc_type, f"placed in {folder} {placed}"


def _blank(row: dict[str, str], column: str, value: str) -> None:
    if not (row.get(column) or "").strip():
        row[column] = value


def scan_knowledge_base(
    knowledge_base: Path,
    rows: list[dict[str, str]],
    *,
    extensions: tuple[str, ...],
    report: list[str] | None = None,
) -> list[dict[str, str]]:
    """
    Add rows for files that are not already in the register.

    Reads files at the knowledge-base root, and every file under ``01_Source_raw``
    including type subfolders. A new file in ``00_Inbox`` or directly in
    ``01_Source_raw`` is moved into the matching type folder when the filename
    matches a document type, and renamed with its KB ID. An inbox file named
    ``TN-*.md`` is a tool note. Markdown, text, and HTML are copied to the
    cleaned folder and are not OCRed. A title that matches nothing stays in
    the inbox. Lines for those files are appended to ``report``.
    """
    if report is None:
        report = []
    staging = load_staging_rows(knowledge_base)
    known = {(row.get("file_name") or "").replace("\\", "/") for row in rows}
    known_names = {Path(name).name for name in known}
    updated = list(rows)
    by_id = {(row.get("doc_id") or ""): row for row in updated}
    seen_paths: set[Path] = set()
    for path, relative in _iter_source_files(knowledge_base, extensions):
        if path.resolve() in seen_paths:
            continue
        seen_paths.add(path.resolve())
        if relative in known or path.name in known_names:
            continue
        original_name = path.name
        original_relative = relative
        matched = matching_doc_ids(original_name, list(updated) + staging)
        if len(matched) > 1:
            report.append(
                "Left unsorted: "
                f"{original_relative} (more than one row matches this title or TN number: "
                f"{', '.join(matched)})"
            )
            continue
        tool_note = is_inbox_tool_note(original_relative, original_name)
        decision = (TOOL_NOTE_FOLDER, "tool_note") if tool_note else None
        if len(matched) == 1 and matched[0] in by_id:
            doc_id = matched[0]
            existing = by_id[doc_id]
            current = resolve_source_file(knowledge_base, existing.get("file_name", ""))
            if current is not None and current.resolve() != path.resolve():
                report.append(
                    f"Left unsorted: {original_relative} ({doc_id} already has a different file)"
                )
                continue
            path, relative, doc_type, placement = _place_by_title(
                knowledge_base, path, original_relative, doc_id, decision
            )
            if placement.startswith("left unsorted"):
                report.append(f"Left unsorted: {original_relative} ({placement})")
                continue
            existing["file_name"] = relative
            _blank(existing, "title", title_from_filename(re.sub(r"^KB-\d+_", "", original_name)))
            if tool_note:
                _mark_tool_note(existing, fresh=False)
            elif doc_type:
                _blank(existing, "doc_type", doc_type)
            existing["sha256"] = file_sha256(path)
            copy_plain_text(knowledge_base / "02_Source_cleaned", doc_id, path)
            report.append(f"{doc_id}: reused for {relative}")
            known.add(relative)
            known_names.add(original_name)
            known_names.add(path.name)
            continue
        row = empty_row()
        if len(matched) == 1:
            doc_id = matched[0]
            source = next((item for item in staging if item.get("doc_id") == doc_id), None)
            if source:
                for key in row:
                    if source.get(key):
                        row[key] = source[key]
            row["doc_id"] = doc_id
        else:
            row["doc_id"] = next_kb_id(updated + staging)
        path, relative, doc_type, placement = _place_by_title(
            knowledge_base, path, original_relative, row["doc_id"], decision
        )
        row["file_name"] = relative
        _blank(row, "title", title_from_filename(re.sub(r"^KB-\d+_", "", original_name)))
        _blank(row, "licence", "unknown - check")
        _blank(row, "superseded", "unknown - check")
        _blank(row, "ingest_status", "not started")
        moved = not placement.startswith("left unsorted")
        if tool_note and moved:
            _mark_tool_note(row, fresh=True)
        else:
            _blank(row, "doc_type", doc_type)
            _blank(row, "pipeline_status", "not_started")
        row["sha256"] = file_sha256(path)
        if not (row.get("notes") or "").strip():
            if tool_note and moved:
                row["notes"] = (
                    "Added by register scan as a tool note in 07_Portal_web_content/Tool_notes. "
                    "Draft, needs LandIS sign-off. "
                    "Title is taken from the file name and is unverified."
                )
            elif placement:
                row["notes"] = (
                    f"Added by register scan, {placement}. Set the licence before OCR. "
                    "Title is taken from the file name and is unverified."
                )
            else:
                row["notes"] = (
                    "Added by register scan. Set the licence before OCR. "
                    "Title is taken from the file name and is unverified."
                )
        _blank(row, "metadata_note", "title taken from the file name, unverified")
        copy_plain_text(knowledge_base / "02_Source_cleaned", row["doc_id"], path)
        updated.append(row)
        by_id[row["doc_id"]] = row
        known.add(relative)
        known_names.add(original_name)
        known_names.add(path.name)
    return updated


def refresh_hashes(
    knowledge_base: Path, rows: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Fill sha256 where the file is present and the hash is missing or the file changed."""
    updated: list[dict[str, str]] = []
    for row in rows:
        copy = dict(row)
        path = resolve_source_file(knowledge_base, copy.get("file_name", ""))
        if path is not None:
            digest = file_sha256(path)
            if copy.get("sha256") and copy["sha256"] != digest:
                copy["notes"] = (
                    (copy.get("notes") or "").rstrip()
                    + " File hash changed since the last scan."
                ).strip()
            copy["sha256"] = digest
        updated.append(copy)
    return updated


def already_ingested(row: dict[str, str]) -> bool:
    """True when this row has already been embedded."""
    return row.get("ingest_status") == "ingested" or row.get("pipeline_status") == "indexed"


def ready_to_index(
    row: dict[str, str],
    *,
    include_unreviewed: bool = False,
    force: bool = False,
) -> bool:
    """Whether a row may be embedded. Licence is a hard gate.

    An ingested row is skipped unless ``force`` is set, so a later bulk run
    only embeds documents that are not already in the index.
    """
    if not licence_allows_external_processing(row.get("licence", "")):
        return False
    if row.get("ingest_status") == "excluded" or row.get("pipeline_status") == "excluded":
        return False
    if already_ingested(row) and not force:
        return False
    if row.get("pipeline_status") in {"cleaned", "indexed"}:
        return True
    if row.get("ingest_status") in {"cleaned", "labelled", "ingested"}:
        return True
    if include_unreviewed and row.get("pipeline_status") in {"ocr_done", "needs_review"}:
        return True
    return False


def worked_example_rows() -> list[dict[str, str]]:
    """Rows D01–D10 from the corpus register workbook, October 2026."""
    examples = [
        ("D01", "NATMAP legend record, association 411 (Denbigh 1)", "natmap_legend_export.csv", "database record", "national", "1:250,000", "2020", "LandIS licence", "no", "LandIS definitive", "structured - one chunk per record", "all", "AK", "One row per association; approx 297 records in the export", "1 Soil mapping units"),
        ("D02", "Soil series description, Brockhurst series", "series_descriptions_export.csv", "database record", "national", "national", "2020", "LandIS licence", "no", "LandIS definitive", "structured - one chunk per record", "researcher", "AK", "Linked to D01 by component series names", ""),
        ("D03", "Bulletin 14, Soils of South West England", "bulletin_14.pdf", "survey report", "Devon, Cornwall, Somerset", "regional", "1984", "LandIS licence", "no", "LandIS definitive", "prose - section level", "researcher", "", "Scanned - needs OCR. Old but not superseded; confirm with soil scientists", ""),
        ("D04", "Soilscapes user guide", "soilscapes_guide.pdf", "brochure", "national", "national", "2019", "open", "no", "LandIS definitive", "prose - section level", "general public", "", "", ""),
        ("D05", "LandIS data licensing and terms page", "landis.org.uk/licensing", "web page", "national", "national", "2024", "open", "no", "LandIS definitive", "prose - section level", "all", "", "Changes over time - needs a refresh routine after handover", ""),
        ("D06", "HOST class definitions", "host_classes_export.csv", "database record", "national", "national", "2018", "LandIS licence", "no", "LandIS definitive", "structured - one chunk per record", "researcher", "", "29 classes", ""),
        ("D07", "LandIS Soils Glossary", "soils_glossary.html", "reference", "national", "national", "2021", "open", "no", "LandIS definitive", "structured - one chunk per record", "all", "", "One chunk per term. Candidate controlled vocabulary.", ""),
        ("D08", "Avery, Soil Classification for England and Wales", "avery_1980.pdf", "monograph", "national", "national", "1980", "LandIS licence", "no", "LandIS definitive", "prose - section level", "researcher", "", "Old and still definitive - do not confuse age with superseded", ""),
        ("D09", "Pedotransfer functions for bulk density (journal paper)", "bulk_density_2011.pdf", "journal paper", "national", "national", "2011", "unknown - check", "no", "third-party academic", "prose - section level", "researcher", "", "Check we have the right to ingest before any work is done on it", ""),
        ("D10", "Assistant scope and limits statement", "TO BE WRITTEN", "policy", "national", "national", "2026", "open", "no", "LandIS definitive", "structured - one chunk per record", "all", "", "Does not exist yet. One short paragraph per limit.", ""),
    ]
    rows: list[dict[str, str]] = []
    for item in examples:
        row = empty_row()
        (
            row["doc_id"],
            row["title"],
            row["file_name"],
            row["document_type"],
            row["place"],
            row["spatial_scale"],
            row["publication_year"],
            row["licence"],
            row["superseded"],
            row["authority_tier"],
            row["chunking_mode"],
            row["audience"],
            row["filled_in_by"],
            row["notes"],
            row["subject_primary"],
        ) = item
        row["ingest_status"] = "not started"
        row["pipeline_status"] = "not_started"
        rows.append(row)
    return rows
