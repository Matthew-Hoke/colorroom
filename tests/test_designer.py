"""Tests for the palette tools: collecting colors, contrast, color vision, ramps, exports."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import numpy as np
import pytest

from colorroom.core import designer
from colorroom.core.colorspace import contrast_ratio, hex_to_key, key_to_oklab
from colorroom.core.designer import NamedPalette, PaletteColor, Project

SAMPLE = ["#211D38", "#FFD291", "#A16491", "#514169", "#79537D", "#392558"]


# --- the palette you collect -----------------------------------------------------------


def test_adding_skips_duplicates_and_respects_the_limit():
    palette = NamedPalette()
    assert palette.add(["#b47a3c", "B47A3C", "#fff"]) == (2, 1, 0)
    assert palette.hexes() == ["#B47A3C", "#FFFFFF"]
    many = [f"#{value:06X}" for value in range(1, 40)]
    added, duplicates, dropped = palette.add(many)
    assert len(palette.colors) == designer.MAX_COLORS
    assert (added, duplicates, dropped) == (designer.MAX_COLORS - 2, 0, 39 - (designer.MAX_COLORS - 2))


def test_locked_colors_survive_remove_and_clear():
    palette = NamedPalette(colors=[PaletteColor("#111111"), PaletteColor("#222222", locked=True), PaletteColor("#333333")])
    assert palette.remove([0, 1]) == 1
    assert palette.hexes() == ["#222222", "#333333"]
    assert palette.clear_unlocked() == 1
    assert palette.hexes() == ["#222222"]


def test_move_and_sort():
    palette = NamedPalette()
    palette.add(["#FF0000", "#00FF00", "#0000FF", "#808080", "#FFFFFF"])
    assert palette.move(0, 2) == 2 and palette.hexes()[:3] == ["#00FF00", "#0000FF", "#FF0000"]
    palette.sort("lightness")
    lightness = [key_to_oklab(hex_to_key(color))[0] for color in palette.hexes()]
    assert lightness == sorted(lightness, reverse=True)
    palette.sort("hue")
    assert palette.hexes()[:3] == ["#FF0000", "#00FF00", "#0000FF"]
    assert set(palette.hexes()[3:]) == {"#808080", "#FFFFFF"}  # grays go last


def test_project_round_trip(tmp_path):
    project = Project()
    project.current.name = "Brand"
    project.current.colors = [PaletteColor("#B47A3C", "Clay", True), PaletteColor("#1E2A3B")]
    project.new_palette("Brand")
    project.current.add(["#ffffff"])
    assert [palette.name for palette in project.palettes] == ["Brand", "Brand 2"]
    path = tmp_path / "work.colorroom"
    project.save(path)
    loaded = Project.load(path)
    assert loaded.active == 1
    assert [palette.name for palette in loaded.palettes] == ["Brand", "Brand 2"]
    first = loaded.palettes[0].colors[0]
    assert (first.hex, first.name, first.locked) == ("#B47A3C", "Clay", True)
    assert loaded.palettes[1].hexes() == ["#FFFFFF"]


@pytest.mark.parametrize("text", ["", "not json", "[1, 2]", '{"format": "something else"}', '{"format": "colorroom-palettes"}'])
def test_project_refuses_other_files(text):
    with pytest.raises(ValueError, match="not a Colorroom palette file"):
        Project.from_json(text)


def test_project_ignores_bad_entries_instead_of_failing():
    text = json.dumps(
        {
            "format": "colorroom-palettes",
            "active": 99,
            "palettes": [{"name": "A", "colors": [{"hex": "#12345"}, {"hex": "zzzzzz"}, "#abc", {"hex": "#010203", "name": 5}]}, 7],
        }
    )
    project = Project.from_json(text)
    assert project.active == 0 and len(project.palettes) == 1
    assert project.current.hexes() == ["#AABBCC", "#010203"]


def test_parse_hex_list_finds_codes_in_free_text():
    text = "Primary #b47a3c, dark 1E2A3B; white #fff. Width 100 and 2026 are not colors, nor is #12345."
    assert designer.parse_hex_list(text) == ["#B47A3C", "#1E2A3B", "#FFFFFF"]
    assert designer.parse_hex_list("#B47A3C #b47a3c") == ["#B47A3C"]
    assert designer.parse_hex_list("nothing here") == []


# --- values ----------------------------------------------------------------------------


def test_color_values():
    assert designer.to_cmyk("#000000") == (0, 0, 0, 100)
    assert designer.to_cmyk("#FFFFFF") == (0, 0, 0, 0)
    assert designer.to_cmyk("#FF0000") == (0, 100, 100, 0)
    assert designer.to_cmyk("#00FFFF") == (100, 0, 0, 0)
    assert designer.rgb_text("#B47A3C") == "180, 122, 60"
    assert designer.hsl_text("#808080") == "0°, 0%, 50%"
    table = designer.values_table(["#FF0000"], ["Signal"]).splitlines()
    assert table[0].split("\t")[1] == "HEX"
    assert table[1].split("\t") == ["Signal", "#FF0000", "255, 0, 0", "0°, 100%, 50%", "0, 100, 100, 0"]


# --- contrast --------------------------------------------------------------------------


def test_contrast_grades_follow_wcag_thresholds():
    assert designer.contrast_grade(21) == designer.AAA
    assert designer.contrast_grade(7.0) == designer.AAA
    assert designer.contrast_grade(6.99) == designer.AA
    assert designer.contrast_grade(4.5) == designer.AA
    assert designer.contrast_grade(4.49) == designer.AA_LARGE
    assert designer.contrast_grade(3.0) == designer.AA_LARGE
    assert designer.contrast_grade(2.99) == designer.FAIL
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)


def test_contrast_table_and_readable_pairs():
    table = designer.contrast_table(SAMPLE)
    assert len(table) == len(SAMPLE) and all(len(row) == len(SAMPLE) for row in table)
    assert table[0][0] == pytest.approx(1.0)
    assert table[0][1] == pytest.approx(table[1][0])
    pairs = designer.readable_pairs(SAMPLE)
    assert pairs[0][:2] == ("#211D38", "#FFD291") and pairs[0][2] > 11
    assert all(ratio >= 4.5 for _, _, ratio in pairs)
    assert [ratio for _, _, ratio in pairs] == sorted((ratio for _, _, ratio in pairs), reverse=True)


# --- color vision ----------------------------------------------------------------------


def test_simulation_keeps_grays_and_removes_the_missing_signal():
    for kind in ("protanopia", "deuteranopia", "tritanopia", "achromatopsia"):
        for gray in ("#000000", "#808080", "#FFFFFF"):
            seen = designer.simulate(gray, kind)
            channels = [int(seen[i : i + 2], 16) for i in (1, 3, 5)]
            assert max(channels) - min(channels) <= 2, (kind, gray, seen)
    assert designer.simulate("#B47A3C", "normal") == "#B47A3C"
    gray = designer.simulate("#B47A3C", "achromatopsia")
    assert gray[1:3] == gray[3:5] == gray[5:7]
    # Red and green are opposite hues for typical vision. Without red or green cones they
    # become the same hue (they can still differ in brightness).
    import math

    def hue(color: str) -> float:
        _, a, b = key_to_oklab(hex_to_key(color))
        return math.degrees(math.atan2(b, a))

    assert abs(hue("#FF0000") - hue("#00C000")) > 90
    for kind in ("protanopia", "deuteranopia"):
        assert abs(hue(designer.simulate("#FF0000", kind)) - hue(designer.simulate("#00C000", kind))) < 12


def test_simulation_works_on_whole_images():
    rgb = np.array([[[255, 0, 0], [0, 255, 0]], [[0, 0, 255], [128, 128, 128]]], dtype=np.uint8)
    seen = designer.simulate_rgb(rgb, "deuteranopia")
    assert seen.shape == rgb.shape and seen.dtype == np.uint8
    assert designer.simulate("#FF0000", "deuteranopia") == "#{:02X}{:02X}{:02X}".format(*seen[0, 0])
    assert np.array_equal(designer.simulate_rgb(rgb, "normal"), rgb)


def test_confusable_pairs_are_reported():
    assert ("#D03030", "#5E8A1E") in designer.confusable_pairs(["#D03030", "#5E8A1E", "#1E2A8A"], "deuteranopia", close=0.09)
    assert designer.confusable_pairs(["#000000", "#FFFFFF"], "deuteranopia") == []


# --- tints and shades ------------------------------------------------------------------


@pytest.mark.parametrize("color", ["#5A3CB0", "#FFD291", "#211D38", "#808080", "#FFFFFF", "#000000"])
def test_ramp_contains_the_color_and_runs_light_to_dark(color):
    ramp = designer.tint_ramp(color)
    assert [step for step, _ in ramp] == list(designer.RAMP_STEPS)
    assert color in [value for _, value in ramp]
    lightness = [key_to_oklab(hex_to_key(value))[0] for _, value in ramp]
    assert lightness == sorted(lightness, reverse=True)


def test_ramp_of_a_gray_stays_gray_and_exports():
    ramp = designer.tint_ramp("#808080")
    for _, value in ramp:
        assert value[1:3] == value[3:5] == value[5:7]
    css = designer.ramp_css("brand", ramp)
    assert css.startswith(":root {") and "--brand-500:" in css and css.count("--brand-") == 11
    assert designer.ramp_tailwind("brand", ramp).startswith("brand: {") and "950: '#" in designer.ramp_tailwind("brand", ramp)
    assert designer.slug("Deep Plum!") == "deep-plum" and designer.slug("???") == "color"


# --- formats for design apps -----------------------------------------------------------


def test_swatches_svg_is_valid_and_carries_every_color():
    svg = designer.swatches_svg(["#b47a3c", "#1E2A3B"], ["Clay & <Sand>", ""], "My \"brand\"")
    root = ET.fromstring(svg)  # must be well-formed XML, whatever the names contain
    namespace = "{http://www.w3.org/2000/svg}"
    fills = [rect.get("fill") for rect in root.iter(f"{namespace}rect")]
    assert fills == ["#B47A3C", "#1E2A3B"]
    texts = [text.text for text in root.iter(f"{namespace}text")]
    assert "#B47A3C" in texts and "Clay & <Sand>" in texts
    assert int(root.get("width")) > int(root.get("height"))


def test_design_tokens_format():
    data = json.loads(designer.design_tokens(["#B47A3C", "#1E2A3B", "#ffffff"], ["Clay", "Clay", ""], "Brand Kit"))
    tokens = data["brand-kit"]
    assert tokens["clay"] == {"$type": "color", "$value": "#B47A3C"}
    assert tokens["clay-2"]["$value"] == "#1E2A3B"
    assert tokens["color-3"]["$value"] == "#FFFFFF"


# --- roles for the mockups -------------------------------------------------------------


def test_roles_always_give_readable_text():
    for variant in range(8):
        roles = designer.assign_roles(SAMPLE, variant)
        assert contrast_ratio(roles.text, roles.background) >= 4.5
        assert contrast_ratio(roles.on_primary, roles.primary) >= 4.5 or roles.on_primary in ("#000000", "#FFFFFF")
        assert set(roles.as_dict()) == {"Background", "Surface", "Text", "Primary", "Secondary", "Accent"}
    light = designer.assign_roles(SAMPLE, 0)
    dark = designer.assign_roles(SAMPLE, 1)
    assert light.background == "#FFD291" and dark.background == "#211D38"


def test_roles_cope_with_tiny_and_low_contrast_palettes():
    assert designer.assign_roles([]) is None
    one = designer.assign_roles(["#808080"])
    assert one.background == "#808080" and one.text in ("#000000", "#FFFFFF")
    close = designer.assign_roles(["#777777", "#808080"])
    assert contrast_ratio(close.text, close.background) >= 4.5


# --- gradient map ----------------------------------------------------------------------


def test_gradient_runs_dark_to_light_through_the_palette():
    stops = designer.gradient_stops(SAMPLE)
    assert stops[0] == "#211D38" and stops[-1] == "#FFD291" and sorted(stops) == sorted(SAMPLE)
    table = designer.gradient_lut(SAMPLE)
    assert table.shape == (256, 3) and table.dtype == np.uint8
    assert tuple(table[0]) == (0x21, 0x1D, 0x38) and tuple(table[255]) == (0xFF, 0xD2, 0x91)
    brightness = table.astype(int) @ np.array([54, 183, 19])
    assert np.all(np.diff(brightness) >= -40)  # brightness climbs, allowing for rounding


def test_gradient_map_recolors_by_brightness():
    rgb = np.array([[[0, 0, 0], [255, 255, 255]], [[128, 128, 128], [255, 0, 0]]], dtype=np.uint8)
    mapped = designer.gradient_map(rgb, SAMPLE)
    assert mapped.shape == rgb.shape and mapped.dtype == np.uint8
    assert tuple(mapped[0, 0]) == (0x21, 0x1D, 0x38) and tuple(mapped[0, 1]) == (0xFF, 0xD2, 0x91)
    allowed = {tuple(row) for row in designer.gradient_lut(SAMPLE)}
    assert all(tuple(pixel) in allowed for pixel in mapped.reshape(-1, 3))
    one = designer.gradient_lut(["#5A3CB0"])  # a single color runs from black to white through itself
    assert tuple(one[0]) == (0, 0, 0) and tuple(one[255]) == (255, 255, 255) and tuple(one[128]) != tuple(one[0])
    assert designer.gradient_lut([]).sum() == 0
