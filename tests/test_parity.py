"""Parity with Colorroom 1.7.

tests/fixtures/expected/*.json holds what the ORIGINAL JavaScript produced for each
test image (see tools/make_fixtures.mjs). These tests check the Python engine gives the
same answers: exact for counts, orders and picks; tiny tolerances for decimal values.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest

from colorroom.core import palettes
from colorroom.core.colorspace import hex_to_key, key_to_hex, key_to_oklab
from colorroom.core.exporters import inventory_csv_blocks
from colorroom.core.shades import related_shades

from conftest import sha1_u32

PAGE_SIZE = 12  # rows per page in the old inventory
RELATED_PAGE_SIZE = 8  # shades per page in the old inspector
HEAD = 500


def close(value, expected, tolerance=1e-9):
    return value == pytest.approx(expected, rel=tolerance, abs=tolerance)


def same_point(point, expected) -> bool:
    return close(point.x, expected["x"], 1e-12) and close(point.y, expected["y"], 1e-12)


def check_summary(values, expected):
    values = np.asarray(values)
    assert values.size == expected["count"]
    assert values[:HEAD].tolist() == expected["head"]
    assert sha1_u32(values) == expected["sha1"]


def test_totals(case):
    _, index, expected = case
    ready = expected["ready"]
    assert (index.width, index.height) == (ready["width"], ready["height"])
    assert index.distinct == ready["distinct"]
    assert index.visible_pixels == ready["visiblePixels"]
    assert index.base_color == ready["base"]


def test_orders_counts_and_locations(case):
    _, index, expected = case
    check_summary(index.keys, expected["hexOrder"])
    check_summary(index.keys[index.frequency_order], expected["frequencyOrder"])
    check_summary(index.keys[index.variety_order], expected["varietyOrder"])
    check_summary(index.counts, expected["counts"])
    check_summary(index.locations, expected["locations"])


def test_candidates(case):
    _, index, expected = case
    keys = index.keys[index.candidate_idx]
    assert keys.size == expected["candidates"]["count"]
    assert sha1_u32(keys) == expected["candidates"]["sha1"]
    head = [[int(key), int(weight)] for key, weight in zip(keys[:HEAD], index.candidate_weight[:HEAD])]
    assert head == expected["candidates"]["head"]


def test_balanced_picks(case):
    _, index, expected = case
    for label, keys in expected["balanced"].items():
        limit, exponent = label.split("@")
        picked = [int(index.keys[position]) for position in index.balanced(int(limit), float(exponent))]
        assert picked == keys, label


def test_image_palettes(case):
    _, index, expected = case
    for item in expected["palettes"]:
        colors = palettes.image_palette(index, item["size"], item["mode"])
        assert colors == [entry["color"] for entry in item["entries"]], item
        for color, entry in zip(colors, item["entries"]):
            made = palettes.palette_entry(index, color)
            assert made.source_color == entry["source"]["color"]
            assert same_point(made.point, entry["source"]["point"])


def test_nearest_image_color(case):
    _, index, expected = case
    probes = [entry["color"] for entry in expected["paletteSources"]]
    for probe, entry in zip(probes, expected["paletteSources"]):
        made = palettes.palette_entry(index, probe)
        if made.source_color == entry["source"]["color"]:
            assert same_point(made.point, entry["source"]["point"])
            continue
        # The original compared in 32-bit floats and, above 250,000 colors, only against
        # coarse groups. A different answer is fine if ours is at least as close.
        target = np.array(key_to_oklab(hex_to_key(probe)))
        ours = np.array(key_to_oklab(hex_to_key(made.source_color)))
        theirs = np.array(key_to_oklab(hex_to_key(entry["source"]["color"])))
        assert np.sum((ours - target) ** 2) <= np.sum((theirs - target) ** 2) + 1e-7


def test_pixel_picks(case):
    _, index, expected = case
    for item in expected["pixels"]:
        pick = index.pick_pixel(item["x"], item["y"], item["limit"])
        if item["pick"] is None:
            assert pick is None
            continue
        assert pick is not None
        assert pick.color == item["pick"]["color"]
        assert pick.allowed == item["pick"]["allowed"], item
        assert same_point(pick.point, item["pick"]["point"])


def test_related_shades(case):
    _, index, expected = case
    for item in expected["inspect"]:
        base = index.index_of(hex_to_key(item["color"]))
        related = related_shades(index, base)
        assert len(related) == item["total"]
        keys = index.keys[related.indices]
        assert sha1_u32(keys) == item["sha1"], item["color"]
        for position, (key, distance, tone) in enumerate(item["head"]):
            assert int(keys[position]) == key
            assert close(float(related.distances[position]), distance)
            assert int(related.tones[position]) == tone

        for page in item["pages"]:
            positions = related.select(page["tone"])
            assert positions.size == page["total"]
            start = page["page"] * RELATED_PAGE_SIZE
            shown = related.entries(positions[start : start + RELATED_PAGE_SIZE])
            assert [entry.color for entry in shown] == [entry["color"] for entry in page["related"]]
            for entry, wanted in zip(shown, page["related"]):
                assert same_point(entry.point, wanted["point"])
                assert close(entry.difference, wanted["difference"])
            summaries = related.tone_summaries()
            assert [(s.name, s.tone, s.count, s.sample.color) for s in summaries] == [
                (tone["name"], tone["tone"], tone["count"], tone["color"]) for tone in page["tones"]
            ]
            assert same_point(index.locate(base), page["point"])

        for shade_range in item["ranges"]:
            assert related.count_within(shade_range["maximum"]) == shade_range["total"]
            positions = related.select(None, shade_range["maximum"])[:RELATED_PAGE_SIZE]
            assert [entry.color for entry in related.entries(positions)] == [
                entry["color"] for entry in shade_range["shades"]
            ]


def test_inventory_pages(case):
    _, index, expected = case
    for item in expected["pages"]:
        order = index.order(item["sort"], item["limit"], item["prefix"])
        assert order.size == item["total"], item
        start = item["page"] * PAGE_SIZE
        rows = order[start : start + PAGE_SIZE]
        assert [index.hex(int(row)) for row in rows] == [row["color"] for row in item["rows"]], item
        for row, wanted in zip(rows, item["rows"]):
            assert index.count(int(row)) == wanted["pixels"]
            assert index.share(int(row)) == wanted["share"]
            assert same_point(index.locate(int(row)), wanted["point"])


def test_inventory_csv(case):
    _, index, expected = case
    text = "".join(inventory_csv_blocks(index))
    assert text[:400] == expected["csv"]["head"]
    assert len(text) == expected["csv"]["length"]
    assert hashlib.sha1(text.encode("utf-8")).hexdigest() == expected["csv"]["sha1"]


def test_hex_helpers_round_trip():
    assert key_to_hex(hex_to_key("#b47a3c")) == "#B47A3C"
    assert hex_to_key("#fff") == 0xFFFFFF
