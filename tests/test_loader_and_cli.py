"""Tests for opening image files and for the command-line tool."""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image, ImageCms

from colorroom import cli
from colorroom.core import loader
from colorroom.core.index import ImageError
from colorroom.core.loader import load_image

from conftest import FIXTURES, load_rgba


def checker(width=6, height=4) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    image[::2, ::2] = (255, 0, 0)
    image[1::2, 1::2] = (0, 0, 255)
    image[0, -1] = (10, 200, 30)
    return image


def test_png_round_trip(tmp_path):
    rgba = load_rgba("blobs")
    path = tmp_path / "blobs copy.png"
    Image.fromarray(rgba, "RGBA").save(path)
    loaded = load_image(path)
    assert np.array_equal(loaded.rgba, rgba)
    assert (loaded.name, loaded.path, loaded.frames, loaded.color_managed) == ("blobs copy.png", str(path), 1, False)
    assert (loaded.width, loaded.height) == (220, 160)


def test_opens_bytes_and_pillow_images():
    rgba = load_rgba("tiny")
    buffer = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buffer, "PNG")
    assert np.array_equal(load_image(buffer.getvalue(), name="clip").rgba, rgba)
    assert np.array_equal(load_image(Image.fromarray(rgba, "RGBA")).rgba, rgba)


@pytest.mark.parametrize("extension", [".jpg", ".webp", ".bmp", ".gif", ".tif", ".avif"])
def test_common_formats_open(tmp_path, extension):
    path = tmp_path / f"picture{extension}"
    try:
        Image.fromarray(checker(32, 24), "RGB").save(path)
    except (OSError, KeyError, ValueError):
        pytest.skip(f"This Pillow build cannot write {extension}")
    loaded = load_image(path)
    assert loaded.rgba.shape == (24, 32, 4)
    assert loaded.rgba.dtype == np.uint8
    assert int(loaded.rgba[..., 3].min()) == 255


def test_animated_image_frames(tmp_path):
    frames = [Image.new("RGB", (8, 8), color) for color in ("#FF0000", "#00FF00", "#0000FF")]
    path = tmp_path / "anim.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80)
    first = load_image(path)
    assert first.frames == 3 and first.frame == 0
    assert tuple(first.rgba[0, 0, :3]) == (255, 0, 0)
    assert tuple(load_image(path, frame=2).rgba[0, 0, :3]) == (0, 0, 255)
    assert load_image(path, frame=99).frame == 2  # clamped to the last frame


def test_palette_transparency_is_kept(tmp_path):
    image = Image.new("P", (4, 4), 0)
    image.putpalette([255, 0, 0, 0, 255, 0] + [0] * 762)
    image.putpixel((1, 1), 1)
    path = tmp_path / "indexed.png"
    image.save(path, transparency=0)
    rgba = load_image(path).rgba
    assert rgba[0, 0, 3] == 0
    assert tuple(rgba[1, 1]) == (0, 255, 0, 255)


def test_sixteen_bit_gray_is_scaled_not_clipped(tmp_path):
    values = np.array([[0, 32768, 65535]], dtype=np.uint16)
    path = tmp_path / "deep.png"
    Image.fromarray(values).save(path)
    rgba = load_image(path).rgba
    assert rgba[0, :, 0].tolist() == [0, 128, 255]


def test_exif_rotation_is_applied(tmp_path):
    image = Image.fromarray(checker(6, 4), "RGB")
    exif = Image.Exif()
    exif[0x0112] = 6  # the camera was turned: rotate 90 degrees clockwise to view
    path = tmp_path / "turned.png"
    image.save(path, exif=exif)
    loaded = load_image(path)
    assert (loaded.width, loaded.height) == (4, 6)
    # Turning the picture clockwise moves its old top-right corner to the bottom right.
    assert tuple(int(value) for value in loaded.rgba[-1, -1, :3]) == (10, 200, 30)


def test_srgb_profile_keeps_pixels_exact(tmp_path):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    path = tmp_path / "tagged.png"
    Image.fromarray(checker(), "RGB").save(path, icc_profile=profile)
    loaded = load_image(path)
    assert not loaded.color_managed
    assert np.array_equal(loaded.rgba[..., :3], checker())


def test_other_profiles_are_converted(tmp_path, monkeypatch):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    path = tmp_path / "tagged.png"
    rgba = load_rgba("blobs")
    Image.fromarray(rgba, "RGBA").save(path, icc_profile=profile)
    monkeypatch.setattr(loader, "_is_srgb", lambda profile: False)  # pretend it is not sRGB
    loaded = load_image(path)
    assert loaded.color_managed
    assert np.array_equal(loaded.rgba[..., 3], rgba[..., 3])  # transparency untouched
    visible = rgba[..., 3] > 0
    difference = np.abs(loaded.rgba[..., :3].astype(int) - rgba[..., :3].astype(int))[visible]
    assert int(difference.max()) <= 2  # sRGB to sRGB is (nearly) the identity


def test_broken_profile_does_not_stop_the_image(tmp_path):
    path = tmp_path / "broken.png"
    Image.fromarray(checker(), "RGB").save(path, icc_profile=b"not a profile")
    loaded = load_image(path)
    assert not loaded.color_managed
    assert np.array_equal(loaded.rgba[..., :3], checker())


def test_bad_files_give_friendly_errors(tmp_path):
    with pytest.raises(ImageError, match="could not be found"):
        load_image(tmp_path / "missing.png")
    text = tmp_path / "notes.png"
    text.write_text("this is not an image")
    with pytest.raises(ImageError, match="could not be decoded"):
        load_image(text)
    truncated = tmp_path / "cut.png"
    truncated.write_bytes((FIXTURES / "images" / "noise.png").read_bytes()[:2000])
    with pytest.raises(ImageError, match="could not be decoded"):
        load_image(truncated)


def test_oversized_file_is_refused_before_decoding(tmp_path):
    path = tmp_path / "huge.png"
    Image.new("L", (6001, 4000), 128).save(path, compress_level=1)
    with pytest.raises(ImageError, match="24 megapixels"):
        load_image(path)


def test_file_beyond_pillows_own_limit_gives_a_friendly_error(tmp_path, monkeypatch):
    path = tmp_path / "enormous.png"
    Image.fromarray(checker(64, 64), "RGB").save(path)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)  # stands in for a truly enormous file
    with pytest.raises(ImageError, match="24 megapixels"):
        load_image(path)


@pytest.mark.parametrize("format_name", ["PPM", "TGA", "PCX", "ICO", "SGI"])
def test_formats_the_app_does_not_offer_are_refused(tmp_path, format_name):
    """Only the advertised formats reach a decoder, even if the file is named like a PNG."""
    path = tmp_path / "disguised.png"
    Image.fromarray(checker(8, 8), "RGB").save(path, format_name)
    with pytest.raises(ImageError, match="could not be decoded"):
        load_image(path)
    with pytest.raises(ImageError, match="could not be decoded"):
        load_image(path.read_bytes())


# --------------------------------------------------------------------------------------
# Command line
# --------------------------------------------------------------------------------------


def test_cli_json(capsys):
    assert cli.main(["analyze", "sample", "--json", "--top", "3"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["width"] == 840 and result["distinctColors"] == 649
    assert [item["hex"] for item in result["mostFrequent"]] == ["#211D38", "#514169", "#30294F"]
    assert len(result["palette"]["colors"]) == 6


def test_cli_text_and_files(capsys, tmp_path):
    image = FIXTURES / "images" / "blobs.png"
    csv_path, ase_path = tmp_path / "all.csv", tmp_path / "brand.ase"
    code = cli.main(
        ["analyze", str(image), "--palette", "custom", "--pick", "#AC8D7C", "--pick", "#312843", "--pick", "#32ccb3",
         "--csv", str(csv_path), "--export", str(ase_path)]
    )  # fmt: skip
    output = capsys.readouterr().out
    assert code == 0
    assert "17,823 distinct colors" in output
    assert "#32CCB3  Accent · 10%" in output
    assert csv_path.read_text().startswith("hex,pixels,percent")
    assert ase_path.read_bytes()[:4] == b"ASEF"


def test_cli_reports_a_missing_file(capsys, tmp_path):
    assert cli.main(["analyze", str(tmp_path / "nope.png")]) == 2
    assert "could not be found" in capsys.readouterr().err
