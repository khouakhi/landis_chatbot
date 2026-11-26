"""Tests for the LandIS structured records builder (pipeline/structured_records.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import structured_records as sr

EXPORT_DIR = sr.resolve_path(sr.load_settings()["structured_data"]["export_dir"])
needs_export = pytest.mark.skipif(
    not EXPORT_DIR.is_dir(), reason="LandIS export folder not present"
)


def _vocab(units: dict[str, str] | None = None) -> sr.Vocabulary:
    return sr.Vocabulary(
        units=units
        or {"percent": "%", "sand_total": "%", "depth": "cm", "area": "km2", "ph": ""},
        labels={"sand_total": "sand", "ph": "pH", "dgley": "depth to gleying"},
        lu_groups={"AR": "arable"},
    )


def _ctx(no_data: frozenset[str] = frozenset()) -> sr.BuildContext:
    documented = sr.load_settings()["structured_data"]["documented_codes"]
    return sr.BuildContext(
        kb_id="KB-103",
        ingest_date="2026-11-25",
        kb_version="0.1",
        fallback_url="https://example.test/index",
        vocab=_vocab(),
        no_data=no_data,
        documented={
            name: {str(code): text for code, text in codes.items()}
            for name, codes in documented.items()
        },
    )


def _empty_tables(host: dict | None = None) -> sr.Tables:
    return sr.Tables(
        current=[],
        series={},
        props={},
        brief={},
        horizons={},
        correlatives={},
        components={},
        components_by_unit={},
        associations=[],
        non_soil=[],
        assoc_by_unit={},
        alerts={},
        host=host or {},
        classification=[],
        soilscapes=[],
        wrb=[],
    )


# ---------------------------------------------------------------------------
# Cleaning and wording
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["NULL", "null", "", "   ", " NULL ", "None", "nan"])
def test_missing_tokens_become_none(raw: str) -> None:
    assert sr.clean(raw) is None


def test_clean_collapses_whitespace_and_drops_footnote_asterisks() -> None:
    assert sr.clean("  high*  ") == "high"
    assert sr.clean("a    b\tc") == "a b c"
    assert sr.clean(None) is None


def test_measure_leaves_out_unit_when_not_confirmed() -> None:
    vocab = _vocab()
    assert sr.measure(vocab, "ph", "6.3") == "pH 6.3"


def test_measure_writes_percent_without_space() -> None:
    vocab = _vocab()
    assert sr.measure(vocab, "sand_total", "17") == "sand 17%"


def test_with_unit_keeps_space_for_word_units() -> None:
    assert sr.with_unit("160", "mm") == "160 mm"
    assert sr.with_unit("2.15", "%") == "2.15%"
    assert sr.with_unit("25", "") == "25"


def test_fmt_number_adds_thousands_separator_only_above_ten() -> None:
    assert sr.fmt_number("3469") == "3,469"
    assert sr.fmt_number("2801.04") == "2,801"
    assert sr.fmt_number("7") == "7"


def test_profile_reference_drops_brackets_and_prefix() -> None:
    assert sr.profile_reference("(Full description Palmer (1982), p.153)") == (
        "Palmer (1982), p.153"
    )
    assert sr.profile_reference("(Full description Payton (1989), p142") == (
        "Payton (1989), p142"
    )
    assert sr.profile_reference(None) is None


def test_profile_segment_open_bottom_reads_from_upper_depth() -> None:
    ctx = _ctx(no_data=frozenset({"999"}))
    row = {
        "upper_depth": "30",
        "lower_depth": "999",
        "horizon_notation": "C",
        "horizon_description": "Greyish yellow sandy loam.",
    }
    text = sr.profile_segment(ctx, sr.Report(), row)
    assert text == "C (from 30 cm to the bottom of the profile), greyish yellow sandy loam"


def test_documented_999_gives_its_meaning_not_a_gap() -> None:
    ctx = _ctx()
    rep = sr.Report()
    assert sr.documented_meaning(ctx, rep, "dgley", "999") == "no gley layer"
    assert sr.documented_meaning(ctx, rep, "dimp_dp", "999") == "no slowly permeable layer"
    assert sr.documented_meaning(ctx, rep, "dgley", "25") is None


def test_undocumented_999_for_rock_depth_is_still_left_out() -> None:
    ctx = _ctx(no_data=frozenset({"999"}))
    rep = sr.Report()
    assert sr.documented_meaning(ctx, rep, "drock", "999") is None
    assert sr.measured(ctx, rep, "series_properties", "drock", "999") is None


def test_unit_starting_with_percent_follows_value_directly() -> None:
    assert sr.with_unit("18.3", "% vol") == "18.3% vol"
    assert sr.with_unit("55.6", "%") == "55.6%"
    assert sr.with_unit("1.15", "g/cm3") == "1.15 g/cm3"


def test_no_data_code_is_dropped_from_measures() -> None:
    ctx = _ctx(no_data=frozenset({"-8.00"}))
    rep = sr.Report()
    assert sr.measured(ctx, rep, "series_horizons", "bulk_density", "-8.00") is None
    assert rep.skipped["no_data_code.series_horizons.bulk_density"] == 1
    assert sr.measured(ctx, rep, "series_horizons", "bulk_density", "1.20") == "1.20"


def test_host_description_falls_back_to_host_classes() -> None:
    tables = _empty_tables(host={"1": {"host_t": "Free draining permeable soils."}})
    text, from_lookup = sr.host_description(tables, {"host": "1", "host_desc": None})
    assert text == "Free draining permeable soils."
    assert from_lookup is True


def test_host_description_uses_series_value_first() -> None:
    tables = _empty_tables(host={"25": {"host_t": "Other text"}})
    text, from_lookup = sr.host_description(
        tables, {"host": "25", "host_desc": "Slowly permeable soils."}
    )
    assert text == "Slowly permeable soils."
    assert from_lookup is False


def test_pick_puts_priority_records_first() -> None:
    records = [{"chunk_id": f"K-{i}"} for i in range(6)]
    chosen = sr.pick(records, ["K-4", "K-1"], count=4)
    assert [r["chunk_id"] for r in chosen] == ["K-4", "K-1", "K-0", "K-2"]


# ---------------------------------------------------------------------------
# Full build against the real export
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built():
    settings = sr.load_settings()
    return sr.build(settings, EXPORT_DIR, now=datetime(2026, 11, 25, tzinfo=timezone.utc))


@needs_export
def test_counts_match_expected(built) -> None:
    records, manifest, _ = built
    expected = sr.load_settings()["structured_data"]["expected_counts"]
    for name, want in expected.items():
        assert len(records[name]) == want, name
    assert manifest["checks"] == "all passed"


@needs_export
def test_chunk_ids_are_unique_and_every_record_has_text_and_source(built) -> None:
    records, _, _ = built
    ids = [r["chunk_id"] for rows in records.values() for r in rows]
    assert len(ids) == len(set(ids))
    for rows in records.values():
        for r in rows:
            assert r["text"].strip()
            assert r["source_url"]
            assert r["title"]


@needs_export
def test_no_null_like_words_in_any_text(built) -> None:
    records, _, _ = built
    for rows in records.values():
        for r in rows:
            assert not sr.NULL_LIKE_WORD.search(r["text"]), r["chunk_id"]


@needs_export
def test_denchworth_series_matches_export(built) -> None:
    records, _, _ = built
    den = next(r for r in records["series"] if r["title"] == "Denchworth series")
    assert den["chunk_id"] == "KB-103-S-306"
    assert "Denchworth series (symbol Da, series number 306)" in den["text"]
    assert "subgroup 7.12 pelo-stagnogley soils" in den["text"]
    assert "WRB 2006: Eutric Vertic Stagnosols" in den["text"]
    assert "Available water capacity 160 mm" in den["text"]
    assert "Shrink-swell potential: high" in den["text"]
    assert "Denchworth association (712b, 38%)" in den["text"]


@needs_export
def test_denchworth_association_matches_export(built) -> None:
    records, _, _ = built
    assoc = next(r for r in records["associations"] if r["chunk_id"] == "KB-103-A-0712b")
    assert "Denchworth (map unit 712b) is a soil association" in assoc["text"]
    assert "3,469 km2, 2.29% of the mapped area" in assoc["text"]
    assert "Component series: Denchworth 38%; Wickham 19%" in assoc["text"]
    assert assoc["alert_ids"] == ["7"]


@needs_export
def test_sentinel_depths_are_not_written_as_measures(built) -> None:
    records, _, _ = built
    lund = next(r for r in records["series"] if r["chunk_id"] == "KB-103-S-1162")
    assert "depth to gleying 75 cm" in lund["text"]
    assert "999" not in lund["text"]
    assert "no slowly permeable layer" in lund["text"]


@needs_export
def test_non_soil_units_have_one_record(built) -> None:
    records, _, _ = built
    assert len(records["non_soil_units"]) == 1
    assert records["non_soil_units"][0]["chunk_id"] == "KB-103-N-01"


@needs_export
def test_writing_twice_gives_identical_files(built, tmp_path: Path) -> None:
    records, manifest, preview = built
    first = tmp_path / "first"
    second = tmp_path / "second"
    sr.write_outputs(first, records, manifest, preview)
    sr.write_outputs(second, records, manifest, preview)
    for name in list(sr.RECORD_FILES.values()) + ["manifest.json", "PREVIEW.md"]:
        assert (first / name).read_bytes() == (second / name).read_bytes(), name
