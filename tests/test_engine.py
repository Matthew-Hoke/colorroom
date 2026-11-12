"""Tests for the parts of the engine that are new in FABLE or guard against bad input."""

from __future__ import annotations

import json
import struct

import numpy as np
import pytest
from PIL import Image

from colorroom.core import exporters, palettes
from colorroom.core.colorspace import (
    contrast_ratio,
    hex_to_key,
    key_to_oklab,
    mix_oklab,
    oklab_to_key,
    readable_text_color,
)
from colorroom.core.index import MAX_PIXELS, AnalysisCancelled, ColorIndex, ImageError
from colorroom.core.sample import sample_image
from colorroom.core.shades import related_shades

from conftest import load_rgba

# --------------------------------------------------------------------------------------
# Color math
# --------------------------------------------------------------------------------------


def test_oklab_round_trip():
    rng = np.random.default_rng(3)
    for key in [0x000000, 0xFFFFFF, 0x5A3CB0, 0xFF0000, 0x00FF00, 0x0000FF, *rng.integers(0, 1 << 24, 400).tolist()]:
        assert oklab_to_key(*key_to_oklab(key)) == key


def test_mix_and_contrast():
    assert mix_oklab("#000000", "#FFFFFF", 0) == "#000000"
    assert mix_oklab("#000000", "#FFFFFF", 1) == "#FFFFFF"
    middle = hex_to_key(mix_oklab("#000000", "#FFFFFF"))
    assert (middle >> 16) == ((middle >> 8) & 255) == (middle & 255)  # a neutral gray
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21)
    assert readable_text_color("#FFD291") == "#000000"
    assert readable_text_color("#211D38") == "#FFFFFF"


# --------------------------------------------------------------------------------------
# Building the index
# --------------------------------------------------------------------------------------


def test_rejects_fully_transparent_image():
    rgba = np.zeros((4, 4, 4), dtype=np.uint8)
    rgba[..., :3] = 200
    with pytest.raises(ImageError, match="fully transparent"):
        ColorIndex.build(rgba)


def test_rejects_oversized_image():
    rgba = np.zeros((4001, 6000, 4), dtype=np.uint8)
    assert rgba.shape[0] * rgba.shape[1] > MAX_PIXELS
    with pytest.raises(ImageError, match="24 megapixels"):
        ColorIndex.build(rgba)


def test_rejects_wrong_shape():
    with pytest.raises(ValueError):
        ColorIndex.build(np.zeros((4, 4, 3), dtype=np.uint8))


def test_can_be_cancelled():
    with pytest.raises(AnalysisCancelled):
        ColorIndex.build(load_rgba("blobs"), cancelled=lambda: True)


def test_reports_progress():
    seen: list[float] = []
    ColorIndex.build(load_rgba("blobs"), progress=lambda fraction, message: seen.append(fraction))
    assert seen == sorted(seen)
    assert seen[0] == 0.0 and seen[-1] == 1.0


def test_counts_add_up(blobs_index):
    assert int(blobs_index.counts.sum()) == blobs_index.visible_pixels
    assert np.all(np.diff(blobs_index.keys.astype(np.int64)) > 0)  # ascending, no repeats
    assert sorted(blobs_index.variety_order.tolist()) == list(range(blobs_index.distinct))


def test_located_pixels_really_have_that_color(blobs_index):
    index = blobs_index
    for position in index.variety_order[:200]:
        point = index.locate(int(position))
        column, row = index.pixel_at(point.x, point.y)
        red, green, blue, alpha = (int(value) for value in index.rgba[row, column])
        assert alpha > 0
        assert (red << 16) | (green << 8) | blue == int(index.keys[position])


def test_order_rejects_bad_input(sample_index):
    with pytest.raises(ValueError):
        sample_index.order("variety", None, "XYZ")
    with pytest.raises(ValueError):
        sample_index.order("variety", None, "1234567")
    with pytest.raises(ValueError):
        sample_index.order("loudest")


def test_orders_are_permutations_of_the_same_subset(blobs_index):
    for limit in (1, 50, 1000, blobs_index.distinct):
        wanted = sorted(blobs_index.variety_order[:limit].tolist())
        for sort in ("variety", "frequency", "hex"):
            assert sorted(blobs_index.order(sort, limit).tolist()) == wanted


def test_nearest_is_exact_for_image_colors_and_never_worse_than_any_candidate(blobs_index):
    index = blobs_index
    present = int(index.keys[123])
    assert index.nearest(present) == 123
    target = hex_to_key("#00FF88")
    assert index.index_of(target) is None
    best = index.nearest(target)
    assert index.nearest(target) == best  # cached answer is stable
    goal = np.array(key_to_oklab(target))

    def distance(position: int) -> float:
        return float(np.sum((np.array(key_to_oklab(int(index.keys[position]))) - goal) ** 2))

    assert all(distance(best) <= distance(int(other)) + 1e-12 for other in index.candidate_idx)


def test_sample_image_is_stable():
    image = sample_image()
    assert image.shape == (390, 840, 4) and image.dtype == np.uint8
    assert np.array_equal(image, load_rgba("sample"))  # same pixels as the saved fixture


# --------------------------------------------------------------------------------------
# Related shades
# --------------------------------------------------------------------------------------


def test_related_shades_filters(blobs_index):
    related = related_shades(blobs_index, int(blobs_index.frequency_order[0]))
    assert len(related) > 50
    assert np.all(np.diff(related.distances) >= 0)
    everything = related.select()
    assert everything.size == len(related)
    by_tone = [related.select(tone) for tone in range(5)]
    assert sum(positions.size for positions in by_tone) == len(related)
    narrow = related.select(None, 2.0)
    assert 0 < narrow.size < len(related)
    assert all(entry.difference <= 2.0 + 1e-9 for entry in related.entries(narrow))
    for tone, positions in enumerate(by_tone):
        limited = related.select(tone, 2.0)
        assert set(limited.tolist()) == set(positions.tolist()) & set(narrow.tolist())
    assert related_shades(blobs_index, related.base) is related  # cached


def test_gray_base_finds_only_grays():
    index = ColorIndex.build(load_rgba("gray"))
    related = related_shades(index, int(index.frequency_order[0]))
    assert len(related) == index.distinct - 1
    summaries = related.tone_summaries()
    assert [summary.name for summary in summaries] == ["Deep", "Dark", "Midtone", "Bright", "Light"]


# --------------------------------------------------------------------------------------
# Palettes
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("base", ["#5A3CB0", "#FFD291", "#808080", "#000000", "#FFFFFF"])
def test_monochromatic_respects_the_color_count(base):
    for size in palettes.SIZES:
        colors = palettes.suggest_colors("monochromatic", base, size)
        assert len(colors) == size, (base, size)
        assert len(set(colors)) == size
        assert colors[0] == base


@pytest.mark.parametrize("theme", list(palettes.THEMES))
def test_seasonal_respects_the_color_count(theme):
    original = list(palettes.THEMES[theme])
    assert palettes.theme_colors(theme, 5) == original
    for size in palettes.SIZES:
        colors = palettes.theme_colors(theme, size)
        assert len(colors) == size
        if size >= 5:
            assert [color for color in colors if color in original] == original  # all kept, in order
        else:
            assert all(color in original for color in colors)
            assert colors[0] == original[0] and colors[-1] == original[-1]


@pytest.mark.parametrize("mode", palettes.MODES)
def test_every_style_builds_a_palette(sample_index, mode):
    picks = ["#FFD291", "#514169", "#79537D"]
    palette = palettes.build_palette(sample_index, mode, 6, "Autumn", picks)
    assert palette.entries and palette.note
    for entry in palette.entries:
        assert sample_index.index_of(hex_to_key(entry.source_color)) == entry.source_index
        assert 0 <= entry.point.x <= 1 and 0 <= entry.point.y <= 1
    if mode == "custom":
        assert palette.colors == picks
        assert [entry.role for entry in palette.entries] == list(palettes.BRAND_ROLES)
    elif mode not in palettes.IMAGE_MODES:
        assert palette.colors[:3] == picks  # picks always lead the palette
    if palettes.uses_size(mode) and mode in palettes.IMAGE_MODES:
        assert len(palette.entries) == 6


def test_custom_palette_without_picks_is_empty_and_explains(sample_index):
    palette = palettes.build_palette(sample_index, "custom")
    assert palette.entries == ()
    assert "at least three" in palette.note


def test_wheel_palette_uses_the_most_frequent_color_by_default(sample_index):
    palette = palettes.build_palette(sample_index, "complementary")
    assert palette.colors[0] == sample_index.base_color
    assert "most frequent shade" in palette.note
    gray = palettes.build_palette(ColorIndex.build(load_rgba("gray")), "triadic")
    assert "Blue guides" in gray.note


def test_unknown_style_is_rejected(sample_index):
    with pytest.raises(ValueError):
        palettes.build_palette(sample_index, "sparkly")


# --------------------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------------------

COLORS = ["#211D38", "#ffd291", "#A16491"]


def test_palette_text_and_css():
    assert exporters.palette_text(COLORS) == "#211D38, #FFD291, #A16491"
    assert exporters.palette_css(COLORS) == (
        ":root {\n  --palette-1: #211D38;\n  --palette-2: #FFD291;\n  --palette-3: #A16491;\n}\n"
    )


def test_palette_json_and_gpl():
    data = json.loads(exporters.palette_json(COLORS, "Dusk", ["Dominant · 60%"]))
    assert data["name"] == "Dusk"
    assert data["colors"][1] == {"hex": "#FFD291", "rgb": [255, 210, 145]}
    assert data["colors"][0]["role"] == "Dominant · 60%"
    lines = exporters.palette_gpl(COLORS, "Dusk").splitlines()
    assert lines[:2] == ["GIMP Palette", "Name: Dusk"]
    assert lines[-1] == "161 100 145\t#A16491"


def test_palette_ase_structure():
    data = exporters.palette_ase(COLORS, "Dusk")
    assert data[:4] == b"ASEF"
    major, minor, blocks = struct.unpack(">HHI", data[4:12])
    assert (major, minor, blocks) == (1, 0, len(COLORS) + 2)
    position, kinds, values = 12, [], []
    while position < len(data):
        kind, length = struct.unpack(">HI", data[position : position + 6])
        body = data[position + 6 : position + 6 + length]
        kinds.append(kind)
        if kind == 0x0001:
            name_length = struct.unpack(">H", body[:2])[0]
            name = body[2 : 2 + name_length * 2].decode("utf-16-be").rstrip("\0")
            model = body[2 + name_length * 2 : 6 + name_length * 2]
            red, green, blue = struct.unpack(">fff", body[6 + name_length * 2 : 18 + name_length * 2])
            assert model == b"RGB "
            values.append((name, round(red * 255), round(green * 255), round(blue * 255)))
        position += 6 + length
    assert position == len(data)
    assert kinds == [0xC001, 1, 1, 1, 0xC002]
    assert values == [("#211D38", 33, 29, 56), ("#FFD291", 255, 210, 145), ("#A16491", 161, 100, 145)]


def test_save_palette_formats(tmp_path):
    for extension in (".css", ".json", ".gpl", ".ase", ".png", ".txt"):
        target = tmp_path / f"palette{extension}"
        assert exporters.save_palette(COLORS, target, "Dusk") == extension[1:]
        assert target.stat().st_size > 0
    assert Image.open(tmp_path / "palette.png").size[0] > 600
    assert (tmp_path / "palette.txt").read_text() == "#211D38\n#FFD291\n#A16491\n"
    with pytest.raises(ValueError):
        exporters.save_palette(COLORS, tmp_path / "palette.xyz")


def test_inventory_csv_file(tmp_path, sample_index):
    target = tmp_path / "all.csv"
    assert exporters.write_inventory_csv(sample_index, target) == sample_index.distinct
    data = target.read_bytes()
    assert data == "".join(exporters.inventory_csv_blocks(sample_index)).encode("utf-8")
    lines = data.decode().split("\r\n")
    assert lines[0] == "hex,pixels,percent"
    assert len(lines) == sample_index.distinct + 2 and lines[-1] == ""
    total = sum(float(line.split(",")[2]) for line in lines[1:-1])
    assert total == pytest.approx(100, abs=1e-5)


def test_cancelled_csv_leaves_no_file(tmp_path, sample_index):
    target = tmp_path / "all.csv"
    with pytest.raises(AnalysisCancelled):
        exporters.write_inventory_csv(sample_index, target, cancelled=lambda: True)
    assert not target.exists()
