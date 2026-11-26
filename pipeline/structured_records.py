"""Build knowledge-base records from the LandIS database export.

The builder reads the CSV files in the dated export folder and writes one JSON Lines
file per record type into the records folder, together with ``manifest.json`` and
``PREVIEW.md``. The export folder is only read, never written. No language model is
used: every sentence is assembled from a value in the export, and a field that is
missing is left out of the text rather than filled in.

Run from the ``code`` folder::

    python -m pipeline.structured_records
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

SCRIPT_VERSION = "1.0.0"
CODE_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_PATH = CODE_ROOT / "config" / "settings.yaml"

MISSING_TOKENS = frozenset({"", "NULL", "NONE", "NAN"})
NULL_LIKE_WORD = re.compile(r"\b(NULL|None|nan)\b")

EXPORT_FILES = (
    "association_alerts",
    "association_components",
    "associations_core",
    "classification_definitions",
    "host_classes",
    "series_brief_profile",
    "series_core",
    "series_correlatives",
    "series_horizons",
    "series_properties",
    "soilscapes",
    "subgroup_names",
    "wrb_reference_groups",
)

# Columns the builder reads. A file missing one of these stops the build.
REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    "association_alerts": ("alert_id", "map_unit"),
    "association_components": (
        "map_unit", "seq", "series_no", "series_name", "series_pc",
    ),
    "associations_core": (
        "map_unit", "map_code", "map_unit_name", "simple_description", "geology",
        "dom_soils", "assoc_soils", "site", "crop_land_use", "map_unit_area",
        "map_unit_pc", "ss_id", "soilscape", "wrb_2006", "source_url",
    ),
    "classification_definitions": (
        "category", "subgroup", "major_group", "subgp_name", "state", "definition",
    ),
    "host_classes": ("host", "host_t"),
    "series_brief_profile": (
        "series_no", "reference", "upper_depth", "lower_depth",
        "horizon_notation", "horizon_description",
    ),
    "series_core": (
        "series_no", "series_name", "series_symbol", "subgroup", "subgroup_name",
        "state", "modern_definition", "parent_material", "parent_lithology",
        "wrb_2006", "area_mapped", "percent_cover", "source_url",
    ),
    "series_correlatives": ("old_series_name", "modern_series_no"),
    "series_horizons": (
        "series_no", "lu_group", "horno", "designation", "designation_description",
        "upper_depth", "lower_depth", "sand_total", "silt", "clay", "oc", "ph",
        "bulk_density", "total_porosity", "thv5", "thv1500", "ksat_subvert",
    ),
    "series_properties": (
        "series_no", "host", "host_desc", "awc", "dgley", "dimp_dp", "drock",
        "shrink_swell_desc", "corrosivity_iron", "corrosivity_zinc",
        "pesticide_runoff", "pesticide_leaching", "groundwater_leaching",
    ),
    "soilscapes": (
        "ss_id", "soilscape", "texture", "drainage", "fertility", "landcover",
        "habitats", "carbon", "drains_to", "water_protection", "general_cropping",
        "coverage_ew", "coverage_e", "coverage_w",
    ),
    "wrb_reference_groups": ("wrbcode", "wrb_l1", "description"),
}

RECORD_FILES: dict[str, str] = {
    "series": "series.jsonl",
    "series_horizons": "series_horizons.jsonl",
    "associations": "associations.jsonl",
    "non_soil_units": "non_soil_units.jsonl",
    "classification": "classification.jsonl",
    "soilscapes": "soilscapes.jsonl",
    "wrb": "wrb.jsonl",
}

# Settings unit keys for fields whose unit is shared (e.g. every depth is in cm).
UNIT_OF: dict[str, str] = {
    "dgley": "depth",
    "dimp_dp": "depth",
    "drock": "depth",
    "upper_depth": "depth",
    "lower_depth": "depth",
    "area_mapped": "area",
    "map_unit_area": "area",
    "percent_cover": "percent",
    "series_pc": "percent",
    "map_unit_pc": "percent",
    "coverage_e": "percent",
    "coverage_w": "percent",
    "coverage_ew": "percent",
}

HYDROLOGY_FIELDS = ("awc", "dgley", "dimp_dp", "drock")
HORIZON_FIELDS = (
    "sand_total", "silt", "clay", "oc", "ph", "bulk_density", "total_porosity",
    "thv5", "thv1500", "ksat_subvert",
)
RISK_FIELDS = (
    ("Shrink-swell potential", "shrink_swell_desc"),
    ("Corrosivity to iron", "corrosivity_iron"),
    ("Corrosivity to zinc", "corrosivity_zinc"),
    ("Pesticide runoff", "pesticide_runoff"),
    ("Pesticide leaching", "pesticide_leaching"),
    ("Groundwater leaching", "groundwater_leaching"),
)
ASSOCIATION_FIELDS = (
    ("Geology", "geology"),
    ("Dominant soils", "dom_soils"),
    ("Associated soils", "assoc_soils"),
    ("Site", "site"),
    ("Land use", "crop_land_use"),
)
SOILSCAPE_FIELDS = (
    ("Texture", "texture"),
    ("Drainage", "drainage"),
    ("Fertility", "fertility"),
    ("Land cover", "landcover"),
    ("Habitats", "habitats"),
    ("Carbon", "carbon"),
    ("Drains to", "drains_to"),
    ("Water protection", "water_protection"),
    ("General cropping", "general_cropping"),
)
CLASSIFICATION_LEVELS = {
    "MAJOR": ("major group", "majorgroup"),
    "GROUP": ("group", "group"),
    "SUBGROUP": ("subgroup", "subgroup"),
}


class BuildError(RuntimeError):
    """Raised when the export is missing data or a check fails."""


class Report:
    """Counts of missing values, fallbacks and skipped rows, written to the manifest."""

    def __init__(self) -> None:
        self.missing: Counter[str] = Counter()
        self.fallbacks: Counter[str] = Counter()
        self.skipped: Counter[str] = Counter()

    def note_missing(self, record_type: str, field_name: str) -> None:
        self.missing[f"{record_type}.{field_name}"] += 1


# ---------------------------------------------------------------------------
# Settings and vocabulary
# ---------------------------------------------------------------------------


def load_settings(path: Path = SETTINGS_PATH) -> dict[str, Any]:
    """Return the parsed settings file."""
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def resolve_path(text: str) -> Path:
    """Resolve a settings path against the code folder unless it is absolute."""
    path = Path(text)
    return path if path.is_absolute() else (CODE_ROOT / path).resolve()


@dataclass(frozen=True)
class Vocabulary:
    """Units, labels and land-use names. These are unconfirmed until LandIS checks them."""

    units: Mapping[str, str]
    labels: Mapping[str, str]
    lu_groups: Mapping[str, str]

    def unit(self, field_name: str) -> str:
        return str(self.units.get(UNIT_OF.get(field_name, field_name), "") or "")

    def label(self, field_name: str) -> str:
        return str(self.labels.get(field_name, field_name))


@dataclass(frozen=True)
class BuildContext:
    """Values shared by every builder in one run."""

    kb_id: str
    ingest_date: str
    kb_version: str
    fallback_url: str
    vocab: Vocabulary
    no_data: frozenset[str] = frozenset()
    documented: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


def meaning_of(ctx: BuildContext, field_name: str, value: str | None) -> str | None:
    """Return the documented meaning of a coded value, or None if the code has none."""
    if value is None:
        return None
    return ctx.documented.get(field_name, {}).get(value)


def documented_meaning(
    ctx: BuildContext,
    rep: Report,
    field_name: str,
    value: str | None,
) -> str | None:
    """Like ``meaning_of``, and counts each use in the manifest."""
    meaning = meaning_of(ctx, field_name, value)
    if meaning is not None:
        rep.fallbacks[f"documented_code.{field_name}"] += 1
    return meaning


def measured(
    ctx: BuildContext,
    rep: Report,
    record_type: str,
    field_name: str,
    value: str | None,
) -> str | None:
    """Return a measured value, or None if it is missing or a no-data code."""
    if value is not None and value in ctx.no_data:
        rep.skipped[f"no_data_code.{record_type}.{field_name}"] += 1
        return None
    return value


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def clean(value: str | None) -> str | None:
    """Return the value with whitespace and footnote asterisks removed, or None if missing.

    The export marks missing values as ``NULL``, an empty string or whitespace only.
    Trailing asterisks are footnote markers in the export (e.g. ``high*``) and are dropped.
    """
    if value is None:
        return None
    text = " ".join(value.split()).rstrip("*").strip()
    return None if text.upper() in MISSING_TOKENS else text


def as_float(text: str | None) -> float | None:
    """Return the value as a number, or None if it is missing or not numeric."""
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def code_key(code: str | None) -> tuple[int, int, str]:
    """Sort numeric codes numerically and anything else after them, alphabetically."""
    if code and code.isdigit():
        return (0, int(code), "")
    return (1, 0, code or "")


def fmt_number(text: str) -> str:
    """Add thousands separators to whole numbers of 10 or more."""
    number = as_float(text)
    if number is None or number < 10:
        return text
    return f"{number:,.0f}"


def with_unit(value: str, unit: str) -> str:
    """Join a value and its unit. Units that start with a percent sign follow the value directly."""
    if not unit:
        return value
    return f"{value}{'' if unit.startswith('%') else ' '}{unit}"


def measure(vocab: Vocabulary, field_name: str, value: str | None) -> str | None:
    """Return ``label value unit`` for a field, or None when the value is missing."""
    if value is None:
        return None
    return f"{vocab.label(field_name)} {with_unit(value, vocab.unit(field_name))}"


def strip_end(text: str | None) -> str:
    """Remove trailing full stops, semicolons, colons and spaces."""
    return (text or "").rstrip(" .;:")


def sentence(text: str | None) -> str:
    """Return the text as one sentence ending in a full stop, or an empty string."""
    body = strip_end(text)
    return f"{body}." if body else ""


def first_upper(text: str) -> str:
    """Capitalise the first letter and leave the rest as it is."""
    return text[:1].upper() + text[1:] if text else text


def lower_first(text: str) -> str:
    """Lower-case the first letter and leave the rest as it is."""
    return text[:1].lower() + text[1:] if text else text


def profile_reference(text: str | None) -> str | None:
    """Turn the export's bracketed reference into a short citation.

    ``(Full description Palmer (1982), p.153)`` becomes ``Palmer (1982), p.153``.
    """
    if not text:
        return None
    ref = text.strip()
    if ref.startswith("("):
        ref = ref[1:]
    if ref.endswith(")"):
        ref = ref[:-1]
    ref = ref.strip()
    prefix = "full description "
    if ref.lower().startswith(prefix):
        ref = ref[len(prefix):]
    return ref or None


def area_text(
    vocab: Vocabulary,
    area_field: str,
    area: str | None,
    pc_field: str,
    pc: str | None,
) -> str:
    """Return a sentence about the area and share of a series or map unit."""
    area_part = (
        f"about {with_unit(fmt_number(area), vocab.unit(area_field))}" if area else ""
    )
    pc_part = with_unit(pc, vocab.unit(pc_field)) if pc else ""
    if area and pc:
        return sentence(f"It covers {area_part}, {pc_part} of the mapped area")
    if area:
        return sentence(f"It covers {area_part}")
    if pc:
        return sentence(f"It covers {pc_part} of the mapped area")
    return ""


def source(ctx: BuildContext, rep: Report, url: str | None, kind: str) -> str:
    """Return the record's own source URL, or the fallback URL when the export has none."""
    if url:
        return url
    rep.fallbacks[f"source_url_{kind}"] += 1
    return ctx.fallback_url


def make_record(
    ctx: BuildContext,
    *,
    chunk_id: str,
    title: str,
    section: str,
    text: str,
    entity_codes: list[str],
    source_url: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one record in the chunk schema, with optional extra fields."""
    record: dict[str, Any] = {
        "chunk_id": chunk_id,
        "source_doc_id": ctx.kb_id,
        "doc_type": "database_record",
        "title": title,
        "section": section,
        "text": text,
        "entity_codes": entity_codes,
        "place": [],
        "authority_tier": "LandIS definitive",
        "licence": None,
        "source_url": source_url,
        "ingest_date": ctx.ingest_date,
        "kb_version": ctx.kb_version,
    }
    if extra:
        record.update(extra)
    return record


# ---------------------------------------------------------------------------
# Reading the export
# ---------------------------------------------------------------------------


def read_table(export_dir: Path, name: str) -> list[dict[str, str | None]]:
    """Read one export CSV. Missing values come back as None."""
    path = export_dir / f"{name}.csv"
    if not path.is_file():
        raise BuildError(f"Missing export file: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        header = {column.strip() for column in (reader.fieldnames or [])}
        absent = [col for col in REQUIRED_COLUMNS.get(name, ()) if col not in header]
        if absent:
            raise BuildError(f"{name}.csv lacks columns: {', '.join(absent)}")
        return [
            {key.strip(): clean(value) for key, value in row.items() if key}
            for row in reader
        ]


def unique_by(rows: list[dict[str, Any]], key: str, name: str) -> dict[str, dict[str, Any]]:
    """Index rows by a key, stopping if the key is repeated."""
    index: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        if value in index:
            raise BuildError(f"Duplicate {key} = {value} in {name}")
        index[value] = row
    return index


@dataclass
class Tables:
    """The export, loaded and indexed for the builders."""

    current: list[dict[str, Any]]
    series: dict[str, dict[str, Any]]
    props: dict[str, dict[str, Any]]
    brief: dict[str, list[dict[str, Any]]]
    horizons: dict[tuple[str, str], list[dict[str, Any]]]
    correlatives: dict[str, list[dict[str, Any]]]
    components: dict[str, list[dict[str, Any]]]
    components_by_unit: dict[str, list[dict[str, Any]]]
    associations: list[dict[str, Any]]
    non_soil: list[dict[str, Any]]
    assoc_by_unit: dict[str, dict[str, Any]]
    alerts: dict[str, list[str]]
    host: dict[str, dict[str, Any]]
    classification: list[dict[str, Any]]
    soilscapes: list[dict[str, Any]]
    wrb: list[dict[str, Any]]
    rep: Report = field(default_factory=Report)


def load_tables(export_dir: Path, rep: Report) -> Tables:
    """Read every export file the builders use and index the rows."""
    for name in EXPORT_FILES:
        if not (export_dir / f"{name}.csv").is_file():
            raise BuildError(f"Missing export file: {export_dir / (name + '.csv')}")

    series_rows = read_table(export_dir, "series_core")
    series = unique_by(series_rows, "series_no", "series_core")
    current = sorted(
        (row for row in series_rows if row.get("state") == "L"),
        key=lambda row: code_key(row.get("series_no")),
    )
    current_nos = {row["series_no"] for row in current}

    props_rows = read_table(export_dir, "series_properties")
    props = unique_by(
        [row for row in props_rows if row.get("series_no") in current_nos],
        "series_no",
        "series_properties",
    )
    rep.skipped["properties_non_current_series"] = len(props_rows) - len(props)

    brief: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_table(export_dir, "series_brief_profile"):
        if row.get("series_no") in current_nos:
            brief[row["series_no"]].append(row)

    horizons: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in read_table(export_dir, "series_horizons"):
        key = (row.get("series_no") or "", row.get("lu_group") or "")
        if key[0] in current_nos and key[1]:
            horizons[key].append(row)
        else:
            rep.skipped["horizon_non_current_or_no_group"] += 1

    correlatives: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_table(export_dir, "series_correlatives"):
        if row.get("modern_series_no"):
            correlatives[row["modern_series_no"]].append(row)

    components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    components_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_table(export_dir, "association_components"):
        if row.get("series_no"):
            components[row["series_no"]].append(row)
        if row.get("map_unit"):
            components_by_unit[row["map_unit"]].append(row)

    all_units = read_table(export_dir, "associations_core")
    associations = [
        row for row in all_units if (row.get("map_unit") or "")[:1].isdigit()
    ]
    non_soil = [row for row in all_units if row not in associations]
    assoc_by_unit = {row["map_unit"]: row for row in associations}
    associations.sort(key=lambda row: row["map_unit"])

    alerts: dict[str, list[str]] = defaultdict(list)
    for row in read_table(export_dir, "association_alerts"):
        if row.get("map_unit") and row.get("alert_id"):
            alerts[row["map_unit"]].append(row["alert_id"])

    return Tables(
        current=current,
        series=series,
        props=props,
        brief=brief,
        horizons=horizons,
        correlatives=correlatives,
        components=components,
        components_by_unit=components_by_unit,
        associations=associations,
        non_soil=non_soil,
        assoc_by_unit=assoc_by_unit,
        alerts=alerts,
        host=unique_by(read_table(export_dir, "host_classes"), "host", "host_classes"),
        classification=[
            row
            for row in read_table(export_dir, "classification_definitions")
            if row.get("state") == "L"
        ],
        soilscapes=read_table(export_dir, "soilscapes"),
        wrb=read_table(export_dir, "wrb_reference_groups"),
        rep=rep,
    )


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def host_description(t: Tables, props: dict[str, Any]) -> tuple[str | None, bool]:
    """Return the HOST description and whether it came from ``host_classes.csv``."""
    desc = props.get("host_desc")
    if desc:
        return desc, False
    host = props.get("host")
    if host and host in t.host and t.host[host].get("host_t"):
        return t.host[host]["host_t"], True
    return None, False


def depth_key(row: dict[str, Any]) -> tuple[bool, float]:
    """Sort profile and horizon rows by upper depth; rows without a depth go last."""
    depth = as_float(row.get("upper_depth"))
    return (depth is None, depth or 0.0)


def depth_text(ctx: BuildContext, rep: Report, record_type: str, row: dict[str, Any]) -> str:
    """Return ``upper-lower cm``, or ``from upper cm to the bottom of the profile`` when the
    export's lower depth is the documented code for "below the bottom of the profile"."""
    unit = ctx.vocab.unit("upper_depth")
    upper = measured(ctx, rep, record_type, "upper_depth", row.get("upper_depth"))
    bottom = documented_meaning(ctx, rep, "lower_depth", row.get("lower_depth"))
    if upper and bottom:
        return f"{with_unit(f'from {upper}', unit)} {bottom}"
    lower = measured(ctx, rep, record_type, "lower_depth", row.get("lower_depth"))
    if upper and lower:
        return with_unit(f"{upper}-{lower}", unit)
    return ""


def profile_segment(ctx: BuildContext, rep: Report, row: dict[str, Any]) -> str:
    """Return one horizon of a typical profile.

    Usually ``0-25 cm Ap, dark brown...``. For an open-ended horizon the depth goes in
    brackets after the label: ``C (from 30 cm to the bottom of the profile), greyish...``.
    """
    notation = row.get("horizon_notation") or ""
    depth = depth_text(ctx, rep, "series_brief_profile", row)
    open_ended = meaning_of(ctx, "lower_depth", row.get("lower_depth")) is not None
    if open_ended and notation and depth:
        lead = f"{notation} ({depth})"
    else:
        lead = " ".join(part for part in (depth, notation) if part)
    desc = strip_end(row.get("horizon_description"))
    if desc:
        return f"{lead}, {lower_first(desc)}" if lead else desc
    return lead


def series_record(ctx: BuildContext, t: Tables, s: dict[str, Any]) -> dict[str, Any]:
    """Build the record for one current soil series."""
    vocab, rep = ctx.vocab, t.rep
    no = s["series_no"]
    raw_name = s["series_name"]
    name = raw_name.title()
    symbol = s.get("series_symbol")
    subgroup = s.get("subgroup")
    subgroup_phrase = " ".join(x for x in (subgroup, s.get("subgroup_name")) if x)
    props = t.props.get(no)
    brief = sorted(t.brief.get(no, []), key=depth_key)

    ids = [f"symbol {symbol}"] if symbol else []
    ids.append(f"series number {no}")
    intro = f"{name} series ({', '.join(ids)}) is a soil series of England and Wales"
    if subgroup_phrase:
        intro += f" in subgroup {subgroup_phrase}"
    # The export stores definitions in lower case, so the first letter is capitalised here.
    definition = s.get("modern_definition")
    parts = [sentence(intro), sentence(first_upper(definition) if definition else None)]
    if not definition:
        rep.note_missing("series", "modern_definition")

    layers = []
    if s.get("parent_material"):
        layers.append(f"parent material: {s['parent_material']}")
    else:
        rep.note_missing("series", "parent_material")
    if s.get("parent_lithology"):
        layers.append(f"lithology: {s['parent_lithology']}")
    else:
        rep.note_missing("series", "parent_lithology")
    if layers:
        parts.append(sentence(first_upper("; ".join(layers))))

    if s.get("wrb_2006"):
        parts.append(sentence(f"WRB 2006: {s['wrb_2006']}"))
    else:
        rep.note_missing("series", "wrb_2006")
    parts.append(
        area_text(
            vocab,
            "area_mapped",
            s.get("area_mapped"),
            "percent_cover",
            s.get("percent_cover"),
        )
    )

    if brief:
        reference = next(
            (profile_reference(r.get("reference")) for r in brief if r.get("reference")),
            None,
        )
        segments = [seg for seg in (profile_segment(ctx, rep, r) for r in brief) if seg]
        if segments:
            head = f"Typical profile ({reference}):" if reference else "Typical profile:"
            parts.append(f"{head} {'; '.join(segments)}.")
    else:
        rep.note_missing("series", "brief_profile")

    if props:
        host = props.get("host")
        host_text, from_lookup = host_description(t, props)
        if host_text:
            lead = f"HOST class {host}, " if host else ""
            parts.append(sentence(f"Hydrology: {lead}{host_text}"))
            if from_lookup:
                rep.fallbacks["host_description_from_host_classes"] += 1
        elif host:
            rep.note_missing("series", "host_desc")
        measures = []
        for field_name in HYDROLOGY_FIELDS:
            raw = props.get(field_name)
            meaning = documented_meaning(ctx, rep, field_name, raw)
            if meaning:
                measures.append(meaning)
                continue
            value = measured(ctx, rep, "series_properties", field_name, raw)
            text = measure(vocab, field_name, value)
            if text:
                measures.append(text)
        if measures:
            parts.append(sentence(first_upper("; ".join(measures))))
        for label, field_name in RISK_FIELDS:
            value = props.get(field_name)
            if value:
                parts.append(sentence(f"{label}: {value}"))
    else:
        rep.note_missing("series", "properties")

    former = sorted(
        {
            row["old_series_name"].title()
            for row in t.correlatives.get(no, [])
            if row.get("old_series_name")
        }
    )
    if former:
        parts.append(
            sentence(f"Former series names included in this series: {', '.join(former)}")
        )

    mentions = []
    components = sorted(
        t.components.get(no, []),
        key=lambda row: (-(as_float(row.get("series_pc")) or 0.0), row.get("map_unit") or ""),
    )
    codes = [f"series:{raw_name}", f"series_no:{no}"]
    if symbol:
        codes.append(f"symbol:{symbol}")
    if subgroup:
        codes.append(f"subgroup:{subgroup}")
    for comp in components:
        assoc = t.assoc_by_unit.get(comp.get("map_unit") or "")
        if assoc is None:
            rep.skipped["series_component_not_soil_association"] += 1
            continue
        code = assoc.get("map_code") or assoc["map_unit"]
        pc = comp.get("series_pc")
        detail = f"{code}, {pc}%" if pc else code
        assoc_name = (assoc.get("map_unit_name") or code).title()
        mentions.append(f"{assoc_name} association ({detail})")
        codes.append(f"mapunit:{assoc['map_unit']}")
    if mentions:
        parts.append(sentence(f"Found in soil associations: {'; '.join(mentions)}"))

    section = f"Soil series > {subgroup_phrase}" if subgroup_phrase else "Soil series"
    return make_record(
        ctx,
        chunk_id=f"{ctx.kb_id}-S-{no}",
        title=f"{name} series",
        section=section,
        text=" ".join(p for p in parts if p),
        entity_codes=codes,
        source_url=source(ctx, rep, s.get("source_url"), "series"),
    )


def horizon_sentence(ctx: BuildContext, row: dict[str, Any], rep: Report) -> str:
    """Return one horizon as a sentence, for example ``A (mineral topsoil) 0-20 cm: ...``."""
    head = row.get("designation") or ""
    if row.get("designation_description"):
        head = f"{head} ({row['designation_description']})".strip()
    depth = depth_text(ctx, rep, "series_horizons", row)
    lead = " ".join(x for x in (head, depth) if x)
    values = []
    for field_name in HORIZON_FIELDS:
        value = measured(ctx, rep, "series_horizons", field_name, row.get(field_name))
        if value is None:
            rep.note_missing("series_horizons", field_name)
            continue
        values.append(measure(ctx.vocab, field_name, value) or "")
    if lead and values:
        return sentence(f"{lead}: {', '.join(values)}")
    return sentence(lead or ", ".join(values))


def build_series(ctx: BuildContext, t: Tables) -> list[dict[str, Any]]:
    """One record per current soil series."""
    return [series_record(ctx, t, s) for s in t.current]


def build_horizons(ctx: BuildContext, t: Tables) -> list[dict[str, Any]]:
    """One record per series and land-use group, listing its horizons."""
    records = []
    keys = sorted(t.horizons, key=lambda k: (code_key(k[0]), k[1]))
    for no, lu in keys:
        s = t.series[no]
        name = s["series_name"].title()
        lu_label = ctx.vocab.lu_groups.get(lu, lu)
        rows = sorted(t.horizons[(no, lu)], key=lambda r: code_key(r.get("horno")))
        sentences = [horizon_sentence(ctx, r, t.rep) for r in rows]
        lead = sentence(f"{name} series, horizon properties under {lu_label}")
        codes = [f"series:{s['series_name']}", f"series_no:{no}", f"lu_group:{lu}"]
        if s.get("series_symbol"):
            codes.append(f"symbol:{s['series_symbol']}")
        records.append(
            make_record(
                ctx,
                chunk_id=f"{ctx.kb_id}-H-{no}-{lu}",
                title=f"{name} series: horizon properties under {lu_label}",
                section="Soil series > Horizon properties",
                text=" ".join([lead] + [x for x in sentences if x]),
                entity_codes=codes,
                source_url=source(ctx, t.rep, s.get("source_url"), "series"),
            )
        )
    return records


def build_associations(ctx: BuildContext, t: Tables) -> list[dict[str, Any]]:
    """One record per soil association (map unit with a numeric code)."""
    vocab, rep = ctx.vocab, t.rep
    records = []
    for a in t.associations:
        unit = a["map_unit"]
        code = a.get("map_code") or unit
        name = (a.get("map_unit_name") or code).title()
        intro = (
            f"{name} (map unit {code}) is a soil association in the National Soil Map "
            "of England and Wales"
        )
        parts = [
            sentence(f"{intro}: {a['simple_description']}")
            if a.get("simple_description")
            else sentence(intro)
        ]
        for label, field_name in ASSOCIATION_FIELDS:
            if a.get(field_name):
                parts.append(sentence(f"{label}: {a[field_name]}"))
            else:
                rep.note_missing("associations", field_name)

        comps = [c for c in t.components_by_unit.get(unit, []) if c.get("series_name")]
        comps.sort(
            key=lambda c: (-(as_float(c.get("series_pc")) or 0.0), as_float(c.get("seq")) or 0.0)
        )
        items = [
            f"{c['series_name'].title()} {with_unit(c['series_pc'], '%')}"
            for c in comps
            if c.get("series_pc")
        ]
        if items:
            parts.append(sentence(f"Component series: {'; '.join(items)}"))
            total = sum(as_float(c.get("series_pc")) or 0.0 for c in comps)
            if total < 99.5:
                parts.append(
                    sentence(f"The listed series add up to {total:g}% of the map unit")
                )
        else:
            rep.note_missing("associations", "component_series")

        ss_id = a.get("ss_id")
        if ss_id:
            if a.get("soilscape"):
                parts.append(sentence(f"Soilscape: {a['soilscape']} (class {ss_id})"))
            else:
                parts.append(sentence(f"Soilscape class {ss_id}"))
        if a.get("wrb_2006"):
            parts.append(sentence(f"WRB 2006: {a['wrb_2006']}"))
        parts.append(
            area_text(
                vocab,
                "map_unit_area",
                a.get("map_unit_area"),
                "map_unit_pc",
                a.get("map_unit_pc"),
            )
        )

        codes = [f"mapunit:{unit}", f"mapcode:{code}"]
        codes += [f"series:{c['series_name']}" for c in comps]
        if ss_id:
            codes.append(f"soilscape:{ss_id}")
        records.append(
            make_record(
                ctx,
                chunk_id=f"{ctx.kb_id}-A-{unit}",
                title=f"{name} association",
                section=f"Soil associations > {code}",
                text=" ".join(p for p in parts if p),
                entity_codes=codes,
                source_url=source(ctx, rep, a.get("source_url"), "association"),
                extra={
                    "alert_ids": sorted(t.alerts.get(unit, []), key=code_key),
                },
            )
        )
    return records


def build_non_soil(ctx: BuildContext, t: Tables) -> list[dict[str, Any]]:
    """One record listing the non-soil map units (for example rock, canal, lake)."""
    if not t.non_soil:
        return []
    items = []
    codes = []
    for a in t.non_soil:
        code = a["map_unit"]
        name = first_upper((a.get("map_unit_name") or code).lower())
        item = f"{name} (map unit {code})"
        desc = a.get("simple_description")
        if desc and desc.lower() != name.lower():
            item += f": {desc}"
        items.append(item)
        codes.append(f"mapunit:{code}")
    text = sentence(
        "The National Soil Map legend also includes non-soil map units: "
        + "; ".join(items)
    )
    return [
        make_record(
            ctx,
            chunk_id=f"{ctx.kb_id}-N-01",
            title="Non-soil map units in the LandIS legend",
            section="Soil associations > Non-soil map units",
            text=text,
            entity_codes=codes,
            source_url=source(ctx, t.rep, None, "non_soil"),
        )
    ]


def build_classification(ctx: BuildContext, t: Tables) -> list[dict[str, Any]]:
    """One record per current major group, group or subgroup of the classification."""
    records = []
    for row in t.classification:
        category = row.get("category") or ""
        code = row.get("subgroup")
        if not code and category == "MAJOR" and row.get("major_group"):
            # The "Unclassified" row has no subgroup code, only major group 00.
            code = row["major_group"]
            t.rep.fallbacks["classification_code_from_major_group"] += 1
        if category not in CLASSIFICATION_LEVELS or not code:
            t.rep.skipped["classification_unknown_level"] += 1
            continue
        level, prefix = CLASSIFICATION_LEVELS[category]
        name = row.get("subgp_name")
        if not name:
            t.rep.note_missing("classification", "subgp_name")
        display = first_upper(name or f"{level} {code}")

        levels = []
        major = row.get("major_group")
        if major:
            levels.append(f"Major group {major}")
        if category in ("GROUP", "SUBGROUP"):
            group_code = code if category == "GROUP" else code.rsplit(".", 1)[0]
            levels.append(f"Group {group_code}")
        if category == "SUBGROUP":
            levels.append(f"Subgroup {code}")
        section = f"Classification > {' > '.join(levels)}" if levels else "Classification"

        parts = [
            sentence(
                f"{display} is a {level} in the classification of soils of England "
                f"and Wales, code {code}"
            )
        ]
        if row.get("definition"):
            parts.append(sentence(f"Definition: {row['definition']}"))
        else:
            t.rep.note_missing("classification", "definition")
        records.append(
            make_record(
                ctx,
                chunk_id=f"{ctx.kb_id}-C-{category[0]}-{code}",
                title=f"{display} ({level} {code})",
                section=section,
                text=" ".join(parts),
                entity_codes=[f"{prefix}:{code}"],
                source_url=source(ctx, t.rep, None, "classification"),
            )
        )
    return records


def build_soilscapes(ctx: BuildContext, t: Tables) -> list[dict[str, Any]]:
    """One record per Soilscapes class that has a texture (sea and water are left out)."""
    records = []
    for row in t.soilscapes:
        ss_id, name = row.get("ss_id"), row.get("soilscape")
        if not ss_id or not name:
            t.rep.skipped["soilscape_missing_id_or_name"] += 1
            continue
        if name.lower() == "sea" or not row.get("texture"):
            t.rep.skipped["soilscape_no_texture_or_sea"] += 1
            continue
        parts = [sentence(f"Soilscape class {ss_id}: {name}")]
        for label, field_name in SOILSCAPE_FIELDS:
            if row.get(field_name):
                parts.append(sentence(f"{label}: {row[field_name]}"))
            else:
                t.rep.note_missing("soilscapes", field_name)
        coverage = [
            f"England {with_unit(row['coverage_e'], ctx.vocab.unit('coverage_e'))}"
            if row.get("coverage_e") else "",
            f"Wales {with_unit(row['coverage_w'], ctx.vocab.unit('coverage_w'))}"
            if row.get("coverage_w") else "",
            f"England and Wales {with_unit(row['coverage_ew'], ctx.vocab.unit('coverage_ew'))}"
            if row.get("coverage_ew") else "",
        ]
        coverage = [c for c in coverage if c]
        if coverage:
            parts.append(sentence(f"Coverage: {'; '.join(coverage)}"))
        records.append(
            make_record(
                ctx,
                chunk_id=f"{ctx.kb_id}-SS-{ss_id}",
                title=f"Soilscape class {ss_id}: {first_upper(name)}",
                section="Soilscapes",
                text=" ".join(parts),
                entity_codes=[f"soilscape:{ss_id}"],
                source_url=source(ctx, t.rep, None, "soilscape"),
            )
        )
    return records


def build_wrb(ctx: BuildContext, t: Tables) -> list[dict[str, Any]]:
    """One record per WRB 2006 reference soil group."""
    records = []
    for row in t.wrb:
        code, group = row.get("wrbcode"), row.get("wrb_l1")
        if not code or not group:
            t.rep.skipped["wrb_missing_code_or_name"] += 1
            continue
        parts = [sentence(f"{group} is a WRB 2006 reference soil group, code {code}")]
        if row.get("description"):
            parts.append(sentence(row["description"]))
        records.append(
            make_record(
                ctx,
                chunk_id=f"{ctx.kb_id}-W-{code}",
                title=f"{group} (WRB 2006 code {code})",
                section="WRB 2006 > Reference soil groups",
                text=" ".join(parts),
                entity_codes=[f"wrb:{code}"],
                source_url=source(ctx, t.rep, None, "wrb"),
            )
        )
    return records


# ---------------------------------------------------------------------------
# Checks, gaps, manifest and preview
# ---------------------------------------------------------------------------


def run_checks(
    records: dict[str, list[dict[str, Any]]],
    expected: Mapping[str, int],
    t: Tables,
    kb_id: str,
) -> list[str]:
    """Return a list of problems. An empty list means every check passed."""
    errors: list[str] = []
    for name, want in expected.items():
        got = len(records.get(name, []))
        if got != want:
            errors.append(f"{name}: expected {want} records, found {got}")

    ids = [r["chunk_id"] for rows in records.values() for r in rows]
    for chunk_id, count in Counter(ids).items():
        if count > 1:
            errors.append(f"duplicate chunk_id {chunk_id} ({count} times)")

    for rows in records.values():
        for r in rows:
            cid = r["chunk_id"]
            if not r.get("text"):
                errors.append(f"{cid}: empty text")
            if not r.get("title"):
                errors.append(f"{cid}: empty title")
            if not r.get("source_url"):
                errors.append(f"{cid}: no source_url")
            if NULL_LIKE_WORD.search(r.get("text") or ""):
                errors.append(f"{cid}: NULL, None or nan appears in the text")

    for no in sorted(t.components, key=code_key):
        if no not in t.series:
            errors.append(f"association component series {no} is not in series_core")

    if not any(r["title"] == "Denchworth series" for r in records.get("series", [])):
        errors.append("Denchworth series record is missing")
    assoc = next(
        (r for r in records.get("associations", []) if r["chunk_id"] == f"{kb_id}-A-0712b"),
        None,
    )
    if assoc is None:
        errors.append("association 0712b (Denchworth) is missing")
    elif "3,469 km2" not in assoc["text"] or "2.29%" not in assoc["text"]:
        errors.append("association 0712b text does not carry the export's area and share")
    return errors


def gap_report(t: Tables) -> dict[str, dict[str, Any]]:
    """Current series with no brief profile, no properties or no horizons."""
    current_nos = [row["series_no"] for row in t.current]
    horizon_nos = {no for no, _ in t.horizons}

    def describe(numbers: list[str]) -> dict[str, Any]:
        names = [f"{t.series[n]['series_name'].title()} ({n})" for n in numbers]
        return {"count": len(numbers), "first_10": names[:10]}

    return {
        "no_brief_profile": describe([n for n in current_nos if n not in t.brief]),
        "no_properties": describe([n for n in current_nos if n not in t.props]),
        "no_horizons": describe([n for n in current_nos if n not in horizon_nos]),
    }


def build(
    settings: dict[str, Any],
    export_dir: Path,
    now: datetime | None = None,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any], str]:
    """Build every record type, run the checks, and return records, manifest and preview.

    Raises ``BuildError`` if a file or column is missing, a count is wrong, or a check fails.
    Nothing is written to disk here.
    """
    sd = settings["structured_data"]
    now = now or datetime.now(timezone.utc)
    rep = Report()
    vocab = Vocabulary(
        units=sd["units"], labels=sd["labels"], lu_groups=sd["lu_groups"]
    )
    ctx = BuildContext(
        kb_id=sd["kb_id"],
        ingest_date=sd["ingest_date"],
        kb_version=str(settings.get("kb_version", "")),
        fallback_url=sd["fallback_source_url"],
        vocab=vocab,
        no_data=frozenset(str(code) for code in sd.get("no_data_codes", [])),
        documented={
            field_name: {str(code): text for code, text in codes.items()}
            for field_name, codes in (sd.get("documented_codes") or {}).items()
        },
    )
    t = load_tables(export_dir, rep)

    records = {
        "series": build_series(ctx, t),
        "series_horizons": build_horizons(ctx, t),
        "associations": build_associations(ctx, t),
        "non_soil_units": build_non_soil(ctx, t),
        "classification": build_classification(ctx, t),
        "soilscapes": build_soilscapes(ctx, t),
        "wrb": build_wrb(ctx, t),
    }

    errors = run_checks(records, sd["expected_counts"], t, ctx.kb_id)
    if errors:
        raise BuildError("\n".join(errors))

    gaps = gap_report(t)
    manifest = {
        "kb_id": ctx.kb_id,
        "export_folder": export_dir.name,
        "export_date": sd["export_date"],
        "built_at": now.isoformat(timespec="seconds"),
        "script_version": SCRIPT_VERSION,
        "kb_version": ctx.kb_version,
        "vocabulary_status": sd["vocabulary_status"],
        "files": {
            name: {"file": RECORD_FILES[name], "records": len(records[name])}
            for name in RECORD_FILES
        },
        "expected_counts": dict(sd["expected_counts"]),
        "no_data_codes": sorted(ctx.no_data),
        "missing_fields": dict(sorted(rep.missing.items())),
        "fallbacks": dict(sorted(rep.fallbacks.items())),
        "skipped": dict(sorted(rep.skipped.items())),
        "gaps": gaps,
        "checks": "all passed",
    }
    preview = build_preview(ctx, records, now)
    return records, manifest, preview


def pick(
    records: list[dict[str, Any]],
    priority: list[str],
    count: int = 5,
) -> list[dict[str, Any]]:
    """Return up to ``count`` records, the priority ones first, then the rest in order."""
    by_id = {r["chunk_id"]: r for r in records}
    chosen = [by_id[cid] for cid in priority if cid in by_id]
    for record in records:
        if len(chosen) >= count:
            break
        if record not in chosen:
            chosen.append(record)
    return chosen[:count]


def build_preview(
    ctx: BuildContext,
    records: dict[str, list[dict[str, Any]]],
    now: datetime,
) -> str:
    """Return the PREVIEW.md text: five records from each file, for a human to read."""
    series = records["series"]
    by_name = {r["title"]: r for r in series}
    den = by_name.get("Denchworth series")
    den_id = den["chunk_id"] if den else None
    den_no = den_id.rsplit("-S-", 1)[1] if den_id else None
    lund_id = f"{ctx.kb_id}-S-1162"
    welsh_ids = [f"{ctx.kb_id}-S-{no}" for no in ("50", "228", "305", "1523")]
    welsh_id = next(
        (cid for cid in welsh_ids if any(r["chunk_id"] == cid for r in series)), None
    )
    no_profile_id = next(
        (r["chunk_id"] for r in series if "Typical profile" not in r["text"]), None
    )
    series_priority = [cid for cid in (den_id, lund_id, welsh_id, no_profile_id) if cid]
    horizon_prefixes = tuple(
        f"{ctx.kb_id}-H-{no}-" for no in (den_no, "1162") if no
    )
    horizon_priority = [
        r["chunk_id"]
        for r in records["series_horizons"]
        if r["chunk_id"].startswith(horizon_prefixes)
    ]
    assoc_priority = [f"{ctx.kb_id}-A-0712b", f"{ctx.kb_id}-A-0813f"]

    chosen = {
        "series": pick(series, series_priority),
        "series_horizons": pick(records["series_horizons"], horizon_priority),
        "associations": pick(records["associations"], assoc_priority),
        "non_soil_units": records["non_soil_units"][:1],
        "classification": pick(records["classification"], []),
        "soilscapes": pick(records["soilscapes"], []),
        "wrb": pick(records["wrb"], []),
    }

    lines = [
        "# Preview of structured records",
        "",
        f"Built {now.strftime('%d %B %Y, %H:%M UTC')} by `pipeline/structured_records.py` "
        f"version {SCRIPT_VERSION} from export {ctx.kb_id}. Read this to check the wording. "
        "Units and land-use labels are unconfirmed (see `code/config/settings.yaml`).",
        "",
        "Chosen to show: Denchworth (series, horizons and association 0712b with alerts), "
        "Lund (series 1162), one Welsh-site series, one series with no brief profile, "
        "and the first records of the other files.",
        "",
    ]
    for name in RECORD_FILES:
        rows = chosen[name]
        total = len(records[name])
        lines.append(
            f"## {RECORD_FILES[name]} ({total} record{'' if total == 1 else 's'}, "
            f"showing {len(rows)})"
        )
        lines.append("")
        for r in rows:
            lines.append(f"### `{r['chunk_id']}`")
            lines.append("")
            lines.append(f"- **Title:** {r['title']}")
            lines.append(f"- **Section:** {r['section']}")
            lines.append(f"- **Entity codes:** {', '.join(r['entity_codes'])}")
            lines.append(f"- **Source:** {r['source_url']}")
            if "alert_ids" in r:
                lines.append(f"- **Alert IDs:** {', '.join(r['alert_ids']) or 'none'}")
            lines.append("")
            lines.append(r["text"])
            lines.append("")
    return "\n".join(lines)


def atomic_write(path: Path, text: str) -> None:
    """Write text to a file through a temporary file, so a failed run never leaves half a file."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.chmod(tmp_name, 0o644)  # mkstemp creates private files; the records are shared
    os.replace(tmp_name, path)


def write_outputs(
    records_dir: Path,
    records: dict[str, list[dict[str, Any]]],
    manifest: dict[str, Any],
    preview: str,
) -> None:
    """Write the JSONL files, manifest and preview. Only the records folder is touched."""
    records_dir.mkdir(parents=True, exist_ok=True)
    for name, filename in RECORD_FILES.items():
        body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records[name])
        atomic_write(records_dir / filename, body)
    manifest_text = json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
    atomic_write(records_dir / "manifest.json", manifest_text)
    atomic_write(records_dir / "PREVIEW.md", preview)


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point. Returns 0 on success and 1 if the build stops."""
    parser = argparse.ArgumentParser(description="Build LandIS structured records.")
    parser.add_argument("--export-dir", type=Path, help="Override the export folder.")
    parser.add_argument("--records-dir", type=Path, help="Override the records folder.")
    args = parser.parse_args(argv)

    settings = load_settings()
    sd = settings["structured_data"]
    export_dir = args.export_dir or resolve_path(sd["export_dir"])
    records_dir = args.records_dir or resolve_path(sd["records_dir"])
    try:
        records, manifest, preview = build(settings, export_dir)
    except BuildError as exc:
        print(f"Build stopped:\n{exc}", file=sys.stderr)
        return 1
    write_outputs(records_dir, records, manifest, preview)

    print(f"Wrote records to {records_dir}")
    for name, info in manifest["files"].items():
        print(f"  {info['file']}: {info['records']}")
    gaps = manifest["gaps"]
    print(
        "Gaps: "
        f"no brief profile {gaps['no_brief_profile']['count']}, "
        f"no properties {gaps['no_properties']['count']}, "
        f"no horizons {gaps['no_horizons']['count']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
