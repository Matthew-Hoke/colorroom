"""InDesign bridges: route D (IDML, no InDesign needed) and route A (InDesign through COM).

Made by Opus 5.5 (2026-11-14). For Fable 5.2 to review.

Engine tests build a small IDML file in the test itself. Live tests are marked
``@pytest.mark.indesign`` and run only with ``COLORROOM_LIVE=1`` on a computer with InDesign;
they build their own test document inside InDesign (in the git-ignored ``test-kit/``), work on
copies, and never touch a file outside the project.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import struct
import zipfile
from pathlib import Path

import pytest

from colorroom.core.bridges import idml, indesign_win

PROJECT = Path(__file__).resolve().parents[1]
WORK = PROJECT / "build" / "test-tmp" / "indesign"  # git-ignored, inside the project
LIVE_KIT = PROJECT / "test-kit" / "indesign-live"  # git-ignored
MAKER = Path(__file__).parent / "fixtures" / "indesign" / "make_test_document.jsx"
PACKAGING = "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging"


@pytest.fixture
def work(request) -> Path:
    """A fresh folder for one test, inside the project (no system temp folder)."""
    folder = WORK / re.sub(r"[^\w-]", "_", request.node.name)[:80]
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    return folder


# --------------------------------------------------------------------------------------------
# A small IDML file
# --------------------------------------------------------------------------------------------

GRAPHIC = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<idPkg:Graphic xmlns:idPkg="{PACKAGING}" DOMVersion="21.1">
\t<Color Self="Color/Black" Model="Process" Space="CMYK" ColorValue="0 0 0 100" ColorOverride="Specialblack" Name="Black" ColorEditable="false" ColorRemovable="false" Visible="true" />
\t<Color Self="Color/Cyan" Model="Process" Space="CMYK" ColorValue="100 0 0 0" ColorOverride="Hiddenreserved" Name="Cyan" ColorEditable="false" ColorRemovable="false" Visible="false" />
\t<Color Self="Color/Paper" Model="Process" Space="CMYK" ColorValue="0 0 0 0" ColorOverride="Specialpaper" Name="Paper" ColorEditable="true" ColorRemovable="false" Visible="true" />
\t<Color Self="Color/Brand Blue" Model="Process" Space="RGB" ColorValue="29 78 216" ColorOverride="Normal" Name="Brand Blue" ColorEditable="true" ColorRemovable="true" Visible="true" />
\t<Color Self="Color/Brand Red" Model="Process" Space="CMYK" ColorValue="0 90 85 0" ColorOverride="Normal" Name="Brand Red" ColorEditable="true" ColorRemovable="true" Visible="true" />
\t<Color Self="Color/Spot Green" Model="Spot" Space="CMYK" ColorValue="80 0 90 0" ColorOverride="Normal" Name="Spot Green" ColorEditable="true" ColorRemovable="true" Visible="true" />
\t<Color Self="Color/Sand" Model="Process" Space="LAB" ColorValue="80 5 20" ColorOverride="Normal" Name="Sand" ColorEditable="true" ColorRemovable="true" Visible="true" />
\t<Color Self="Color/Sky &amp; Sea" Model="Process" Space="RGB" ColorValue="14 165 233" ColorOverride="Normal" Name="Sky &amp; Sea" ColorEditable="true" ColorRemovable="true" Visible="true" />
\t<Color Self="Color/u1a" Model="Process" Space="RGB" ColorValue="120 30 160" ColorOverride="Normal" Name="$ID/" ColorEditable="true" ColorRemovable="true" Visible="false" />
\t<Color Self="Color/u1b" Model="Process" Space="RGB" ColorValue="1 2 3" ColorOverride="Normal" Name="$ID/" ColorEditable="true" ColorRemovable="true" Visible="false" />
\t<Tint Self="Tint/Brand Blue 40%25" TintValue="40" BaseColor="Color/Brand Blue" Name="Brand Blue 40%" Visible="true" />
\t<Gradient Self="Gradient/Sunset" Type="Linear" Name="Sunset" Visible="true">
\t\t<GradientStop Self="u9GradientStop0" StopColor="Color/Brand Red" Location="0" />
\t\t<GradientStop Self="u9GradientStop1" StopColor="Color/u1a" Location="100" Midpoint="50" />
\t</Gradient>
</idPkg:Graphic>
"""

DESIGNMAP = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<?aid style="50" type="document" readerVersion="6.0" featureSet="257" product="21.1(56)" ?>
<Document xmlns:idPkg="{PACKAGING}" DOMVersion="21.1" Self="d">
\t<idPkg:Graphic src="Resources/Graphic.xml" />
\t<idPkg:Styles src="Resources/Styles.xml" />
\t<idPkg:Spread src="Spreads/Spread_u10.xml" />
\t<idPkg:Story src="Stories/Story_u20.xml" />
</Document>
"""

STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<idPkg:Styles xmlns:idPkg="{PACKAGING}" DOMVersion="21.1">
\t<RootCharacterStyleGroup Self="u5"><CharacterStyle Self="CharacterStyle/Warning" Name="Warning" FillColor="Color/Brand Red" /></RootCharacterStyleGroup>
</idPkg:Styles>
"""

SPREAD = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<idPkg:Spread xmlns:idPkg="{PACKAGING}" DOMVersion="21.1">
\t<Spread Self="u10">
\t\t<Rectangle Self="u11" FillColor="Color/Brand Blue" StrokeColor="Color/Brand Red" />
\t\t<Rectangle Self="u12" FillColor="Gradient/Sunset" StrokeColor="Swatch/None" />
\t\t<Rectangle Self="u13" FillColor="Tint/Brand Blue 40%25" StrokeColor="Color/Spot Green" />
\t\t<Oval Self="u14" FillColor="Color/u1a" StrokeColor="Color/Sky &amp; Sea" />
\t</Spread>
</idPkg:Spread>
"""

STORY = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<idPkg:Story xmlns:idPkg="{PACKAGING}" DOMVersion="21.1">
\t<Story Self="u20"><ParagraphStyleRange>
\t\t<CharacterStyleRange FillColor="Color/Brand Blue"><Content>Blue</Content></CharacterStyleRange>
\t\t<CharacterStyleRange FillColor="Color/Brand Red"><Content>Red</Content></CharacterStyleRange>
\t</ParagraphStyleRange></Story>
</idPkg:Story>
"""


class _Unseekable(io.RawIOBase):
    """A write-only stream, so zipfile writes data descriptors (as some zip tools do)."""

    def __init__(self) -> None:
        self.data = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, chunk) -> int:
        self.data += chunk
        return len(chunk)


def make_idml(path: Path, *, graphic: str = GRAPHIC, streamed: bool = False, extra: dict | None = None, first: str = "mimetype") -> Path:
    entries = [
        ("mimetype", idml.MIMETYPE, zipfile.ZIP_STORED),
        ("designmap.xml", DESIGNMAP.encode(), zipfile.ZIP_DEFLATED),
        ("META-INF/container.xml", b'<?xml version="1.0"?><container><rootfiles><rootfile full-path="designmap.xml"/></rootfiles></container>', zipfile.ZIP_DEFLATED),
        ("Resources/Graphic.xml", graphic.encode("utf-8"), zipfile.ZIP_DEFLATED),
        ("Resources/Styles.xml", STYLES.encode(), zipfile.ZIP_DEFLATED),
        ("Resources/Preferences.xml", b'<?xml version="1.0"?><Preferences>' + b"x" * 3000 + b"</Preferences>", zipfile.ZIP_STORED),
        ("Spreads/Spread_u10.xml", SPREAD.encode(), zipfile.ZIP_DEFLATED),
        ("Stories/Story_u20.xml", STORY.encode(), zipfile.ZIP_DEFLATED),
    ]
    for name, data in (extra or {}).items():
        entries.append((name, data, zipfile.ZIP_DEFLATED))
    if first != "mimetype":
        entries.insert(0, entries.pop(next(i for i, e in enumerate(entries) if e[0] == first)))
    target = _Unseekable() if streamed else open(path, "wb")
    try:
        with zipfile.ZipFile(target, "w") as bundle:
            for name, data, method in entries:
                info = zipfile.ZipInfo(name, date_time=(2026, 11, 14, 12, 0, 0))
                info.compress_type = method
                bundle.writestr(info, data)
    finally:
        if not streamed:
            target.close()
    if streamed:
        path.write_bytes(bytes(target.data))
    return path


def raw_entries(path: Path) -> dict[str, tuple]:
    """Each entry as stored: (local header + stored bytes, compress type), read independently."""
    data = path.read_bytes()
    out = {}
    with zipfile.ZipFile(path) as bundle:
        for info in bundle.infolist():
            start = info.header_offset
            name_size, extra_size = struct.unpack_from("<HH", data, start + 26)
            begin = start + 30 + name_size + extra_size
            out[info.filename] = (data[start : begin + info.compress_size], info.compress_type, info.CRC)
    return out


# --------------------------------------------------------------------------------------------
# Route D: reading
# --------------------------------------------------------------------------------------------


def test_read_swatches_finds_every_swatch(work):
    doc = idml.read_swatches(make_idml(work / "a.idml"))
    by_name = {item.name: item for item in doc.swatches if item.named}
    blue = by_name["Brand Blue"]
    assert (blue.kind, blue.model, blue.space, blue.values, blue.hex, blue.hex_exact) == ("color", "process", "RGB", [29, 78, 216], "#1D4ED8", True)
    assert (blue.uses, blue.tint_uses) == (2, 1)  # a frame fill and a text run; one tint
    red = by_name["Brand Red"]
    assert (red.space, red.hex_exact, red.uses, red.style_uses, red.stop_uses) == ("CMYK", False, 2, 1, 1)
    assert by_name["Spot Green"].model == "spot"
    assert by_name["Sand"].space == "Lab" and by_name["Sand"].hex
    assert by_name["Sky & Sea"].id == "Color/Sky & Sea" and by_name["Sky & Sea"].uses == 1
    assert by_name["Black"].builtin and not by_name["Black"].editable
    assert by_name["Cyan"].builtin and not by_name["Cyan"].listed
    assert by_name["Paper"].builtin

    tint = by_name["Brand Blue 40%"]
    assert (tint.kind, tint.base, tint.tint, tint.uses) == ("tint", "Color/Brand Blue", 40.0, 1)
    sunset = by_name["Sunset"]
    assert [stop["color"] for stop in sunset.stops] == ["Color/Brand Red", "Color/u1a"]
    assert [stop["hex"] for stop in sunset.stops][1] == "#781EA0"

    unnamed = [item for item in doc.swatches if not item.named]
    assert [(item.id, item.in_use) for item in unnamed] == [("Color/u1a", True), ("Color/u1b", False)]
    assert (doc.spreads, doc.stories, doc.dom_version) == (1, 1, "21.1")


def test_read_never_extracts_anything(work):
    path = make_idml(work / "a.idml")
    before = sorted(p.name for p in work.iterdir())
    idml.read_swatches(path)
    assert sorted(p.name for p in work.iterdir()) == before


def test_hex_math():
    assert idml.color_to_hex("RGB", [29, 78, 216]) == ("#1D4ED8", True)
    assert idml.color_to_hex("CMYK", [0, 0, 0, 0]) == ("#FFFFFF", False)
    assert idml.color_to_hex("Lab", [100, 0, 0])[0] == "#FFFFFF"
    assert idml.color_to_hex("Lab", [0, 0, 0])[0] == "#000000"
    for color in ("#1D4ED8", "#10B981", "#FACC15", "#000000", "#FFFFFF"):
        assert idml.color_to_hex("CMYK", idml.hex_to_values(color, "CMYK"))[0] == color
        lab_hex = idml.color_to_hex("Lab", idml.hex_to_values(color, "Lab"))[0]
        assert all(abs(int(lab_hex[i : i + 2], 16) - int(color[i : i + 2], 16)) <= 1 for i in (1, 3, 5))
    assert idml.normalize_hex("abc") == "#AABBCC"
    with pytest.raises(idml.IdmlError):
        idml.normalize_hex("#12345")


# --------------------------------------------------------------------------------------------
# Route D: rewriting
# --------------------------------------------------------------------------------------------


def test_rewrite_changes_only_the_asked_values(work):
    source = make_idml(work / "a.idml")
    original = source.read_bytes()
    report = idml.rewrite(source, {"#1D4ED8": "#7C3AED", "Brand Red": "#10B981", "Sky & Sea": "#22C55E"}, work / "a - Colorroom A.idml", space="rgb")
    assert report.ok, report.summary()
    assert source.read_bytes() == original  # the original is untouched
    assert [change.name for change in report.changed] == ["Brand Blue", "Brand Red", "Sky & Sea"]
    assert all(change.verified and change.exact for change in report.changed)

    before, after = raw_entries(source), raw_entries(Path(report.saved))
    assert list(before) == list(after)
    assert list(after)[0] == "mimetype" and after["mimetype"][1] == zipfile.ZIP_STORED
    assert after["mimetype"][0] == before["mimetype"][0]
    for name in before:
        if name != "Resources/Graphic.xml":
            assert after[name] == before[name], name
    with zipfile.ZipFile(report.saved) as bundle:
        assert bundle.testzip() is None
        new_text = bundle.read("Resources/Graphic.xml").decode()

    # The swatch XML differs only in those three ColorValue (and Space) attributes.
    def strip(text: str) -> str:
        for name in ("Brand Blue", "Brand Red", "Sky &amp; Sea"):
            text = re.sub(r'(<Color Self="Color/' + re.escape(name) + r'"[^>]*?) Space="\w+" ColorValue="[^"]*"', r"\1", text)
        return text

    assert strip(new_text) == strip(GRAPHIC)
    assert 'Self="Color/Brand Blue" Model="Process" Space="RGB" ColorValue="124 58 237"' in new_text
    assert 'Self="Color/Brand Red" Model="Process" Space="RGB" ColorValue="16 185 129"' in new_text
    assert 'Name="Sky &amp; Sea"' in new_text
    assert {item["name"] for item in report.follows} == {"Brand Blue 40%", "Sunset"}


def test_rewrite_keeps_cmyk_and_reports_it(work):
    report = idml.rewrite(make_idml(work / "a.idml"), {"Brand Red": "#10B981"}, work / "b.idml")
    assert report.ok
    change = report.changed[0]
    assert (change.space_before, change.space_after) == ("CMYK", "CMYK")
    assert change.verified and not change.exact  # profile-less CMYK: close, never claimed exact
    assert "without a color profile" in change.note
    doc = idml.read_swatches(report.saved)
    assert doc.swatch("Brand Red").space == "CMYK"
    assert doc.swatch("Brand Red").hex == "#10B981"  # the plain formula round-trips


def test_rewrite_exact_ink_values_and_spot(work):
    report = idml.rewrite(make_idml(work / "a.idml"), {"Spot Green": {"space": "CMYK", "values": [10, 20, 30, 0]}}, work / "b.idml")
    assert report.ok and "spot color" in report.changed[0].note
    assert idml.read_swatches(report.saved).swatch("Spot Green").values == [10, 20, 30, 0]
    skipped = idml.rewrite(make_idml(work / "c.idml"), {"Spot Green": "#000000"}, work / "d.idml", include_spot=False)
    assert not skipped.changed and "spot" in skipped.skipped[0]["why"] and not (work / "d.idml").exists()


def test_rewrite_skips_with_reasons(work):
    source = make_idml(work / "a.idml")
    report = idml.rewrite(
        source,
        {
            "#000000": "#111111",  # Black: built in
            "Brand Blue 40%": "#222222",  # a tint
            "Sunset": "#333333",  # a gradient
            "#00FFFF": "#444444",  # hidden Cyan ink: never matched
            "#010203": "#555555",  # an unnamed color nothing uses: never matched
            "#781EA0": "#666666",  # an unnamed color in use: changed
            "#ABCDEF": "#777777",  # nothing
        },
        work / "b.idml",
    )
    assert report.ok
    assert [change.id for change in report.changed] == ["Color/u1a"]
    why = {item["what"]: item["why"] for item in report.skipped}
    assert "built into InDesign" in why["'Black'"]
    assert "tint of 'Brand Blue'" in why["Brand Blue 40%"]
    assert "gradient" in why["Sunset"]
    for what in ("#00FFFF", "#010203", "#ABCDEF"):
        assert "no swatch" in why[what]
    assert "Skipped" in report.summary()


def test_rewrite_known_hex_from_indesign(work):
    # InDesign shows Brand Red as #EF4136 with the document's profile; the plain formula says #FF1A26.
    source = make_idml(work / "a.idml")
    report = idml.rewrite(source, {"#EF4136": "#10B981"}, work / "b.idml", known_hex={"Brand Red": "#EF4136"})
    assert [change.name for change in report.changed] == ["Brand Red"]


def test_rewrite_streamed_zip_with_data_descriptors(work):
    source = make_idml(work / "a.idml", streamed=True)
    with zipfile.ZipFile(source) as bundle:
        assert all(info.flag_bits & 0x08 for info in bundle.infolist())
    report = idml.rewrite(source, {"Brand Blue": "#000000"}, work / "b.idml")
    assert report.ok, report.summary()
    before, after = raw_entries(source), raw_entries(Path(report.saved))
    for name in before:
        if name != "Resources/Graphic.xml":
            assert after[name] == before[name]


def test_rewrite_refuses_unsafe_targets(work):
    source = make_idml(work / "a.idml")
    with pytest.raises(idml.IdmlError, match="never writes over"):
        idml.rewrite(source, {"Brand Blue": "#000000"}, source)
    (work / "taken.idml").write_bytes(b"x")
    with pytest.raises(idml.IdmlError, match="already exists"):
        idml.rewrite(source, {"Brand Blue": "#000000"}, work / "taken.idml")
    with pytest.raises(idml.IdmlError, match=".idml"):
        idml.rewrite(source, {"Brand Blue": "#000000"}, work / "b.zip")
    with pytest.raises(idml.IdmlError, match="folder"):
        idml.rewrite(source, {"Brand Blue": "#000000"}, work / "missing" / "b.idml")
    assert (work / "taken.idml").read_bytes() == b"x"


def test_safe_names():
    # zipfile turns "\\" into "/" when writing on Windows, so these are checked directly.
    assert idml._safe_name("Spreads/Spread_u1.xml")
    for name in ("Resources\\Graphic.xml", "a\x00b", "", "a//b", "../a", "C:x.xml"):
        assert not idml._safe_name(name), name


@pytest.mark.parametrize("bad_name", ["../evil.xml", "/abs.xml", "a/../../b.xml", "C:/x.xml", "a/./b.xml"])
def test_unsafe_entry_names_are_refused(work, bad_name):
    path = make_idml(work / "a.idml", extra={bad_name: b"<x/>"})
    with pytest.raises(idml.IdmlError, match="unsafe"):
        idml.read_swatches(path)


def test_refuses_archives_that_are_not_idml(work):
    (work / "plain.idml").write_bytes(b"not a zip at all")
    with pytest.raises(idml.IdmlError, match="not an IDML"):
        idml.read_swatches(work / "plain.idml")
    with pytest.raises(idml.IdmlError, match="not an IDML"):
        idml.read_swatches(make_idml(work / "b.idml", first="designmap.xml"))


def test_refuses_size_lies_and_bombs(work, monkeypatch):
    # The directory says Graphic.xml is 100 bytes; it unpacks to more. Refused, not trusted.
    path = make_idml(work / "a.idml")
    data = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as bundle:
        names = [info.filename for info in bundle.infolist()]
    end = data.rfind(b"PK\x05\x06")
    at = struct.unpack_from("<L", data, end + 16)[0]
    for name in names:
        name_size, extra_size, note_size = struct.unpack_from("<HHH", data, at + 28)
        if data[at + 46 : at + 46 + name_size].decode() == "Resources/Graphic.xml":
            struct.pack_into("<L", data, at + 24, 100)
        at += 46 + name_size + extra_size + note_size
    (work / "lie.idml").write_bytes(bytes(data))
    with pytest.raises(idml.IdmlError, match="damaged"):
        idml.read_swatches(work / "lie.idml")

    # A part that unpacks past the limit is refused while unpacking.
    monkeypatch.setattr(idml, "MAX_XML_BYTES", 2000)
    with pytest.raises(idml.IdmlError, match="too large"):
        idml.read_swatches(make_idml(work / "big.idml"))


def test_refuses_doctype_and_entities(work):
    bomb = GRAPHIC.replace('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>', '<?xml version="1.0"?><!DOCTYPE g [<!ENTITY a "aaaaaaaaaa">]>')
    with pytest.raises(idml.IdmlError, match="declarations"):
        idml.read_swatches(make_idml(work / "a.idml", graphic=bomb))


def test_a_failed_check_keeps_no_file(work, monkeypatch):
    source = make_idml(work / "a.idml")
    real = idml._build_archive

    def broken(archive, replaced):
        out = bytearray(real(archive, replaced))
        at = out.find(b"Preferences>") + 2  # flip a byte inside a part that must not change
        out[at] ^= 0x01
        return bytes(out)

    monkeypatch.setattr(idml, "_build_archive", broken)
    try:
        report = idml.rewrite(source, {"Brand Blue": "#000000"}, work / "b.idml")
    except idml.IdmlError:
        report = None
    assert report is None or not report.ok
    assert not (work / "b.idml").exists()
    assert not any(p.name.endswith(".colorroom-part") for p in work.iterdir())


# --------------------------------------------------------------------------------------------
# Route A without InDesign: the logic around the scripts
# --------------------------------------------------------------------------------------------


def recorded_read(**changes) -> dict:
    """What indesign_read.jsx returns, recorded from InDesign 2026 on the live test document."""
    def color(id, name, space, values, hex, listed=True, model="PROCESS", uses=0, text=0, stop=0, tint=0, editable=True):
        return {"id": id, "name": name, "listed": listed, "named": bool(name), "type": "Color", "kind": "color", "model": model, "space": space,
                "values": values, "hex": hex, "hexExact": space == "RGB", "editable": editable, "uses": uses, "textUses": text,
                "lockedUses": 0, "stopUses": stop, "tintUses": tint}

    data = {
        "program": "InDesign", "programVersion": "21.1.0.56", "document": "t.indd", "path": "", "openedByColorroom": True,
        "modified": False, "intent": "PRINT_INTENT", "blendingSpace": "CMYK", "rgbProfile": "sRGB IEC61966-2.1",
        "cmykProfile": "U.S. Web Coated (SWOP) v2", "pageWidth": 400, "pageHeight": 300, "pages": 2, "spreads": 2,
        "masterSpreads": 1, "layers": [{"name": "Artwork", "visible": True, "locked": False}, {"name": "Background", "visible": True, "locked": False}],
        "swatches": [
            {"id": "14", "name": "None", "listed": True, "type": "Swatch", "kind": "none", "uses": 3, "textUses": 3},
            color("11", "Black", "CMYK", [0, 0, 0, 100], "#231F20", uses=1, editable=False),
            color("15", "Paper", "CMYK", [0, 0, 0, 0], "#FFFFFF"),
            color("239", "CR RGB Blue", "RGB", [29, 78, 216], "#1D4ED8", uses=2, text=1, stop=1, tint=1),
            color("240", "CR CMYK Red", "CMYK", [0, 90, 85, 0], "#EF4136", uses=2, text=1),
            color("241", "CR Spot Green", "CMYK", [80, 0, 90, 0], "#00B259", model="SPOT", uses=2),
            {"id": "244", "name": "CR RGB Blue 40%", "listed": True, "kind": "tint", "base": "239", "tint": 40, "hex": "#A5B8EF", "uses": 1},
            {"id": "245", "name": "CR Gradient", "listed": True, "kind": "gradient", "stops": [{"color": "239", "location": 0, "midpoint": 50}], "uses": 1},
            color("12", "Cyan", "CMYK", [100, 0, 0, 0], "#00AEEF", listed=False, editable=False),
            color("20", "C=100 M=0 Y=0 K=0", "CMYK", [100, 0, 0, 0], "#00AEEF"),
            color("152", "", "CMYK", [0, 0, 0, 0], "#FFFFFF", listed=False, stop=1),
            {"id": "151", "name": "", "listed": False, "kind": "gradient", "stops": [{"color": "152", "location": 0, "midpoint": 50}]},
            color("300", "", "RGB", [120, 30, 160], "#781EA0", listed=False, uses=1),
        ],
        "items": 7, "itemKinds": {"Rectangle": 5, "Oval": 1, "TextFrame": 1}, "lockedItems": 1, "stories": 1, "textRanges": 3,
        "characters": 8, "images": 0, "imageKinds": {}, "fontsNeedingAttention": [], "linksNeedingAttention": [],
    }
    data.update(changes)
    return data


def test_document_from_recorded_read():
    doc = indesign_win._document_from(recorded_read())
    assert doc.color_mode == "CMYK" and doc.pages == 2
    assert doc.swatch("Black").builtin and doc.swatch("Paper").builtin and doc.swatch("Cyan").builtin
    assert not doc.swatch("C=100 M=0 Y=0 K=0").builtin
    unnamed_white, purple = doc.swatch("152"), doc.swatch("300")
    assert not unnamed_white.named and not unnamed_white.in_use  # only a stop of an unused unnamed gradient
    assert not purple.named and purple.in_use
    assert doc.swatch("239").in_use and doc.swatch("CR Gradient").stops[0]["hex"] == "#1D4ED8"
    assert any("1 locked item." in note for note in doc.notes)
    assert any("1 unnamed color is used" in note for note in doc.notes)
    json.dumps(doc.to_dict())  # the window can save it as JSON


def test_plan_matches_like_indesign_shows_it():
    doc = indesign_win._document_from(recorded_read())
    report = idml.ColorReport(route="indesign", source="t.indd")
    steps = indesign_win._plan(
        doc,
        {"#1D4ED8": "#7C3AED", "#EF4136": "#10B981", "#00AEEF": "#000000", "#FFFFFF": "#000000", "#A5B8EF": "#000000", "#781EA0": "#123456", "CR Spot Green": {"space": "CMYK", "values": [1, 2, 3, 4]}},
        "keep",
        True,
        report,
    )
    plan = {entry["item"].id: entry["step"] for entry in steps}
    assert plan["239"] == {"id": "239", "name": "CR RGB Blue", "space": "RGB", "rgb": [124, 58, 237]}
    assert plan["240"] == {"id": "240", "name": "CR CMYK Red", "space": "CMYK", "rgb": [16, 185, 129]}  # InDesign converts
    assert plan["241"]["values"] == [1, 2, 3, 4]
    assert plan["20"]["space"] == "CMYK"  # the listed swatch, not the hidden Cyan ink
    assert "12" not in plan and "152" not in plan
    assert plan["300"]["rgb"] == [18, 52, 86]  # the unnamed color in use
    why = {item["what"]: item["why"] for item in report.skipped}
    assert "built into InDesign" in why["'Paper'"]
    assert "tint of 'CR RGB Blue'" in why["#A5B8EF"]

    rgb_report = idml.ColorReport(route="indesign", source="t.indd")
    rgb_steps = indesign_win._plan(doc, {"#EF4136": "#10B981", "CR Spot Green": "#000000"}, "rgb", False, rgb_report)
    assert [entry["step"]["space"] for entry in rgb_steps] == ["RGB"]
    assert "spot" in rgb_report.skipped[0]["why"]


def test_check_catches_a_changed_document():
    before = indesign_win._document_from(recorded_read())
    after = indesign_win._document_from(recorded_read(layers=[{"name": "Artwork", "visible": True, "locked": False}]))
    report = idml.ColorReport(route="indesign", source="t.indd")
    indesign_win._check(before, after, [], {}, report, "keep")
    assert report.checks["same pages, layers, items, text and fonts"] is False
    assert "layers" in report.notes[-1]


def test_check_verifies_each_value():
    before = indesign_win._document_from(recorded_read())
    swatches = recorded_read()["swatches"]
    for item in swatches:
        if item["id"] == "239":
            item.update(values=[124, 58, 237], hex="#7C3AED")
    after = indesign_win._document_from(recorded_read(swatches=swatches))
    report = idml.ColorReport(route="indesign", source="t.indd")
    steps = indesign_win._plan(before, {"#1D4ED8": "#7C3AED", "#EF4136": "#10B981"}, "keep", True, report)
    results = {"239": {"ok": True, "values": [124, 58, 237]}, "240": {"ok": True, "values": [75.91, 0, 67.76, 0]}}
    indesign_win._check(before, after, steps, results, report, "keep")
    blue, red = report.changed
    assert blue.verified and blue.exact
    assert not red.verified  # the saved file still has the old red: caught
    assert report.checks["every changed color reads back from the saved file as set"] is False


def test_compose_sends_arguments_as_data():
    source = indesign_win._compose(indesign_win.SCRIPTS / "indesign_read.jsx", {"path": "C:/a \u2028 \"b\".indd"})
    assert "//@include" not in source and source.count("CR.quiet = function") == 1  # the lib, once
    assert source.startswith('var CR_ARGS = {"path": "C:/a \\u2028 \\"b\\".indd"};\n')
    assert source.rstrip().endswith("CR.run(main);")
    for name in ("indesign_read", "indesign_apply", "indesign_preview", "indesign_open", "indesign_status"):
        assert indesign_win._script_path(name).is_file()


def test_apply_checks_its_arguments_before_reaching_indesign(work):
    source = work / "a.indd"
    source.write_bytes(b"not really")
    with pytest.raises(indesign_win.IndesignError, match="never writes over"):
        indesign_win.apply(source, {"#000000": "#FFFFFF"}, source)
    with pytest.raises(indesign_win.IndesignError, match=".indd"):
        indesign_win.apply(source, {"#000000": "#FFFFFF"}, work / "b.pdf")
    with pytest.raises(indesign_win.IndesignError, match="InDesign file"):
        indesign_win.apply(work / "a.psd", {"#000000": "#FFFFFF"}, work / "b.indd")
    (work / "b.indd").write_bytes(b"taken")
    with pytest.raises(indesign_win.IndesignError, match="already exists"):
        indesign_win.apply(source, {"#000000": "#FFFFFF"}, work / "b.indd")
    with pytest.raises(indesign_win.IndesignError, match=".png or .jpg"):
        indesign_win.preview(source, work / "p.gif")
    assert source.read_bytes() == b"not really" and (work / "b.indd").read_bytes() == b"taken"


def test_manifest_and_report_for_the_shared_bridge(work):
    from colorroom.core.bridges import base

    doc = indesign_win._document_from(recorded_read())
    manifest = indesign_win.to_manifest(doc, work / "t.colorroom-manifest.json")
    names = [layer.name for layer in manifest.layers]
    assert names == ["CR RGB Blue", "CR CMYK Red", "CR Spot Green", "Unnamed color #781EA0"]  # in use, not built in
    blue = manifest.layer(239)
    assert blue.colors == [("fill", "#1D4ED8"), ("text", "#1D4ED8"), ("gradient", "#1D4ED8")]
    assert blue.parts[0].key == "swatch/239" and "2 frame fills and strokes" in blue.notes[0]
    assert (manifest.program, manifest.color_mode, manifest.width, manifest.height, manifest.stem) == ("InDesign", "CMYK", 400, 300, "t")
    assert manifest.colors()[0].hex == "#1D4ED8"

    color_report = idml.ColorReport(route="indesign", source="t.indd", saved="t - Colorroom A.indd")
    color_report.changed = [
        idml.SwatchChange("239", "CR RGB Blue", "process", "#1D4ED8", "#7C3AED", "#7C3AED", "RGB", "RGB", verified=True, exact=True),
        idml.SwatchChange("240", "CR CMYK Red", "process", "#EF4136", "#10B981", "#1BB680", "CMYK", "CMYK", verified=True, exact=False, note="Kept as CMYK."),
    ]
    color_report.checks = {"no other swatch changed": True}
    color_report.skipped = [{"what": "#A5B8EF", "why": "it is a tint."}]
    shared = indesign_win.to_apply_report(color_report, {"#1D4ED8": "#7C3AED", "#EF4136": "#10B981"})
    assert isinstance(shared, base.ApplyReport) and shared.saved == "t - Colorroom A.indd"
    # A CMYK swatch InDesign converted with the document's profile is done, but close, not exact,
    # and the summary says so with both values (rule changed by Opus 5.5 on 2026-11-14).
    assert [layer.verified for layer in shared.changed] == [True, True]
    assert [part.exact for layer in shared.changed for part in layer.parts] == [True, False]
    assert shared.ok and "wanted #10B981, the file holds #1BB680" in shared.summary() and "converted" in shared.summary()
    assert shared.skipped[0].reason == "it is a tint."
    color_report.changed.pop()
    assert indesign_win.to_apply_report(color_report, {"#1D4ED8": "#7C3AED"}).ok

    bridge = indesign_win.IndesignBridge()
    assert isinstance(bridge, base.Bridge) and bridge.program == "InDesign"
    with pytest.raises(base.BridgeError, match="nothing to export"):
        bridge.apply(work / "t.indd", {"#123456": "#123456"})
    assert issubclass(indesign_win.IndesignBusy, base.AppBusy) and issubclass(indesign_win.IndesignUnavailable, base.NotAvailable)


def test_available_is_a_plain_answer():
    assert indesign_win.available() in (True, False)
    assert indesign_win._process_running("no-such-program-colorroom.exe") is False


# --------------------------------------------------------------------------------------------
# Live: InDesign on this computer (COLORROOM_LIVE=1)
# --------------------------------------------------------------------------------------------

live = pytest.mark.skipif(
    os.environ.get("COLORROOM_LIVE") != "1" or not indesign_win.available(),
    reason="Live InDesign tests run only with COLORROOM_LIVE=1 and InDesign installed.",
)

MAPPING = {"#1D4ED8": "#7C3AED", "#EF4136": "#10B981", "CR Stop Orange": "#FACC15", "CR Spot Green": "#0EA5E9"}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def kit() -> dict:
    """The test document, made by InDesign itself: an .indd and the .idml InDesign exported."""
    if LIVE_KIT.exists():
        shutil.rmtree(LIVE_KIT)  # only ever this test's own folder
    LIVE_KIT.mkdir(parents=True)
    indd, exported = LIVE_KIT / "colorroom-indesign-test.indd", LIVE_KIT / "colorroom-indesign-test.idml"
    answer = indesign_win.run_script(MAKER, {"indd": indesign_win._js_path(indd), "idml": indesign_win._js_path(exported)}, start=True)
    assert answer["ok"] and indd.is_file() and exported.is_file()
    return {"indd": indd, "idml": exported, "doc": indesign_win.read(indd)}


def user_settings() -> dict:
    script = LIVE_KIT / "settings.jsx"
    script.write_text(
        '//@include "indesign_lib.jsx"\n'
        "var s = app.scriptPreferences, p = app.pngExportPreferences;\n"
        "CR.json({ok: true, interaction: CR.enumName(s.userInteractionLevel), units: CR.enumName(s.measurementUnit),"
        " redraw: s.enableRedraw, png: [p.exportResolution, CR.enumName(p.pngExportRange), String(p.pageString)],"
        " docs: (function () { var out = [], i; for (i = 0; i < app.documents.length; i++) { out.push(String(app.documents[i].name)); } return out; })()});\n",
        encoding="utf-8",
    )
    return indesign_win.run_script(script)


@pytest.mark.indesign
@live
def test_live_read(kit):
    doc = kit["doc"]
    assert (doc.pages, doc.spreads, doc.master_spreads) == (2, 2, 1)
    assert [layer.name for layer in doc.layers] == ["Artwork", "Background"]
    assert doc.item_kinds == {"Rectangle": 5, "Oval": 1, "TextFrame": 1} and doc.locked_items == 1
    assert (doc.images, doc.fonts_needing_attention, doc.links_needing_attention) == (0, [], [])
    assert doc.intent == "PRINT_INTENT"
    blue, red, green = doc.swatch("CR RGB Blue"), doc.swatch("CR CMYK Red"), doc.swatch("CR Spot Green")
    assert (blue.space, blue.values, blue.hex, blue.uses, blue.text_uses, blue.locked_uses, blue.stop_uses, blue.tint_uses) == ("RGB", [29, 78, 216], "#1D4ED8", 2, 1, 1, 1, 1)
    assert (red.space, red.values, red.uses, red.text_uses, red.locked_uses) == ("CMYK", [0, 90, 85, 0], 2, 1, 1)
    assert red.hex and not red.hex_exact
    assert (green.model, green.uses) == ("spot", 2)
    assert doc.swatch("CR Stop Orange").stop_uses == 1
    tint = doc.swatch("CR RGB Blue 40%")
    assert (tint.kind, tint.base, tint.tint, tint.uses) == ("tint", blue.id, 40, 1)
    gradient = doc.swatch("CR Gradient")
    assert [stop["hex"] for stop in gradient.stops] == ["#F97316", "#1D4ED8"]
    assert doc.swatch("CR Purple").uses == 1 and not doc.swatch("CR Unused Teal").used


@pytest.mark.indesign
@live
def test_live_apply_round_trip(kit):
    settings = user_settings()
    original = sha256(kit["indd"])
    target = LIVE_KIT / "colorroom-indesign-test - Colorroom A.indd"
    report = indesign_win.apply(kit["indd"], MAPPING, target, export_idml=True)
    assert report.ok, report.summary()
    assert sha256(kit["indd"]) == original  # the original was never opened or changed
    assert user_settings() == settings  # dialogs, units, export settings and open documents as before

    changed = {change.name: change for change in report.changed}
    assert set(changed) == {"CR RGB Blue", "CR CMYK Red", "CR Stop Orange", "CR Spot Green"}
    assert all(change.verified for change in report.changed)
    assert changed["CR RGB Blue"].exact and changed["CR Stop Orange"].exact
    assert changed["CR CMYK Red"].space_after == "CMYK" and changed["CR Spot Green"].model == "spot"

    # An independent read of the saved file: every value, and nothing else changed.
    before, after = kit["doc"], indesign_win.read(target)
    assert after.shape() == before.shape()
    for change in report.changed:
        now = after.swatch(change.name)
        assert now.space == change.space_after and indesign_win._close(now.values, change.values_after, 0.01)
    assert after.swatch("CR RGB Blue").hex == "#7C3AED" and after.swatch("CR Stop Orange").hex == "#FACC15"
    assert [stop["hex"] for stop in after.swatch("CR Gradient").stops] == ["#FACC15", "#7C3AED"]
    assert after.swatch("CR RGB Blue 40%").hex != before.swatch("CR RGB Blue 40%").hex  # the tint followed
    for name in ("CR Purple", "CR Unused Teal", "Black", "Paper"):
        assert after.swatch(name).values == before.swatch(name).values

    exported = idml.read_swatches(report.extra_files["idml"])
    assert exported.swatch("CR RGB Blue").values == [124, 58, 237]


@pytest.mark.indesign
@live
def test_live_apply_rgb_makes_every_hex_exact(kit):
    target = LIVE_KIT / "colorroom-indesign-test - Colorroom B.indd"
    report = indesign_win.apply(kit["indd"], {"#EF4136": "#10B981", "CR Spot Green": "#0EA5E9"}, target, space="rgb")
    assert report.ok, report.summary()
    assert all(change.exact and change.space_after == "RGB" for change in report.changed)
    assert "Converted from CMYK to RGB" in report.changed[0].note
    assert indesign_win.read(target).swatch("CR CMYK Red").hex == "#10B981"


@pytest.mark.indesign
@live
def test_live_preview(kit):
    from PIL import Image

    settings = user_settings()
    first = indesign_win.preview(kit["indd"], LIVE_KIT / "page1.png")
    with Image.open(first) as picture:
        assert picture.size == (400, 300)  # 72 ppi: one pixel per point
        assert picture.convert("RGB").getpixel((50, 50)) == (29, 78, 216)  # inside the blue box
    second = indesign_win.preview(kit["idml"], LIVE_KIT / "page2.jpg", page=2, resolution=144)
    with Image.open(second) as picture:
        assert picture.size == (800, 600)
    with pytest.raises(indesign_win.IndesignError, match="page 3 does not exist"):
        indesign_win.preview(kit["indd"], LIVE_KIT / "page3.png", page=3)
    assert user_settings() == settings


@pytest.mark.indesign
@live
def test_live_idml_route_opens_in_indesign(kit):
    exported = idml.read_swatches(kit["idml"])
    doc = kit["doc"]
    for item in doc.colors():
        if item.named and item.listed:
            assert exported.swatch(item.name).values == item.values, item.name

    known = {item.name: item.hex for item in doc.colors() if item.named and item.hex}
    target = LIVE_KIT / "colorroom-indesign-test - Colorroom D.idml"
    report = idml.rewrite(kit["idml"], MAPPING, target, known_hex=known)
    assert report.ok, report.summary()
    assert {change.name for change in report.changed} == {"CR RGB Blue", "CR CMYK Red", "CR Stop Orange", "CR Spot Green"}

    opened = indesign_win.read(target)  # InDesign opens the rewritten IDML
    for change in report.changed:
        now = opened.swatch(change.name)
        assert now.space == change.space_after and indesign_win._close(now.values, change.values_after, 0.01), change.name
    assert opened.swatch("CR RGB Blue").hex == "#7C3AED"
    shape_before, shape_after = doc.shape(), opened.shape()
    for key in ("pages", "spreads", "layers", "page items", "page item kinds", "locked items", "stories", "characters"):
        assert shape_after[key] == shape_before[key], key
    # InDesign lists swatches in another order after opening any IDML (its own export too).
    assert sorted(shape_after["swatch names"]) == sorted(shape_before["swatch names"])


@pytest.mark.indesign
@live
def test_live_shared_bridge(kit):
    from colorroom.core.bridges import base

    bridge = indesign_win.IndesignBridge()
    manifest = bridge.read(kit["indd"], work_dir=LIVE_KIT / "bridge")
    assert manifest.preview is not None and manifest.preview.is_file()
    assert {layer.name for layer in manifest.layers} == {"CR RGB Blue", "CR CMYK Red", "CR Spot Green", "CR Stop Orange", "CR Purple"}
    report = bridge.apply(kit["indd"], {"#1D4ED8": "#7C3AED", "#F97316": "#FACC15"}, option="Bridge", report_path=LIVE_KIT / "bridge-report.json")
    assert isinstance(report, base.ApplyReport) and report.ok, report.summary()
    assert Path(report.saved).name == "colorroom-indesign-test - Colorroom Bridge.indd"
    assert report.checks["the original file is unchanged"]
    assert json.loads((LIVE_KIT / "bridge-report.json").read_text(encoding="utf-8"))["ok"] is True


REAL_IDML = PROJECT / "test-kit" / "real" / "indesign-real.idml"  # the user's own file: only ever read and copied
REAL_WORK = PROJECT / "test-kit" / "live" / "indesign"


@pytest.mark.indesign
@live
@pytest.mark.skipif(not REAL_IDML.is_file(), reason="The user's real IDML is not in test-kit/real.")
def test_live_real_idml_from_the_user():
    original = sha256(REAL_IDML)
    REAL_WORK.mkdir(parents=True, exist_ok=True)
    copy = REAL_WORK / "indesign-real.idml"
    shutil.copyfile(REAL_IDML, copy)
    for old in REAL_WORK.glob("indesign-real - Colorroom *"):
        old.unlink()

    doc = indesign_win.read(copy)
    exported = idml.read_swatches(copy)
    for item in doc.colors():  # both routes read the same values
        if item.named and item.listed:
            assert exported.swatch(item.name).values == item.values, item.name
    in_use = [item for item in doc.colors() if item.in_use and not item.builtin]
    assert in_use, "the file uses no swatch Colorroom can change"
    mapping = dict(zip((item.hex for item in in_use), ("#7C3AED", "#10B981", "#F59E0B", "#EF4444")))

    # Route A: InDesign opens the IDML, changes the swatches, saves an .indd, reads it back.
    report = indesign_win.apply(copy, mapping, REAL_WORK / "indesign-real - Colorroom A.indd", space="rgb")
    assert report.ok, report.summary()
    assert len(report.changed) == len(mapping) and all(change.exact for change in report.changed)
    after = indesign_win.read(report.saved)
    # InDesign renames swatches named after their values ("C=100 M=90 Y=10 K=0") to the new values.
    renamed = {item.name: after.swatch(item.id).name for item in in_use if after.swatch(item.id).name != item.name}
    assert all(indesign_win._VALUE_NAME.match(old) and indesign_win._VALUE_NAME.match(new) for old, new in renamed.items())
    expected = doc.shape()
    expected["swatch names"] = sorted((kind, renamed.get(name, name)) for kind, name in expected["swatch names"])
    assert after.shape() == expected
    kept = indesign_win.apply(copy, mapping, REAL_WORK / "indesign-real - Colorroom A-cmyk.indd")
    assert kept.ok and all(change.verified and change.space_after == change.space_before for change in kept.changed)

    # Route D: Colorroom rewrites the IDML; InDesign opens it with the new values.
    known = {item.name: item.hex for item in doc.colors() if item.named and item.hex}
    rewritten = idml.rewrite(copy, mapping, REAL_WORK / "indesign-real - Colorroom D.idml", known_hex=known)
    assert rewritten.ok, rewritten.summary()
    opened = indesign_win.read(rewritten.saved)
    for change in rewritten.changed:
        now = opened.swatch(change.name)
        assert now.space == change.space_after and indesign_win._close(now.values, change.values_after, 0.01)
    for key, value in doc.shape().items():
        assert opened.shape()[key] == value, key

    picture = indesign_win.preview(report.saved, REAL_WORK / "indesign-real - Colorroom A - page1.png")
    from PIL import Image

    with Image.open(picture) as image:
        assert image.size == (round(doc.page_width), round(doc.page_height))
    assert sha256(REAL_IDML) == original  # the user's file is untouched


def unnamed_variant(source: Path, target: Path) -> Path:
    """The exported IDML with "CR Purple" turned into an unnamed color, as IDML imports make them."""
    with zipfile.ZipFile(source) as bundle:
        infos = bundle.infolist()
        parts = [(info, bundle.read(info)) for info in infos]
    with zipfile.ZipFile(target, "w") as out:
        for info, data in parts:
            if info.filename.endswith(".xml"):
                data = data.replace(b'"Color/CR Purple"', b'"Color/ucr1"')
                if info.filename == "Resources/Graphic.xml":
                    data = re.sub(rb'(<Color Self="Color/ucr1"[^>]*?)Name="CR Purple"', rb'\1Name="$ID/"', data)
                    data = re.sub(rb'(<Color Self="Color/ucr1"[^>]*?)Visible="true"', rb'\1Visible="false"', data)
                    data = re.sub(rb'<ColorGroupSwatch [^>]*SwatchItemRef="Color/ucr1"[^>]*/>', b"", data)
            out.writestr(info, data, compress_type=info.compress_type)
    return target


@pytest.mark.indesign
@live
def test_live_unnamed_colors(kit):
    variant = unnamed_variant(kit["idml"], LIVE_KIT / "unnamed.idml")
    doc = indesign_win.read(variant)
    purple = [item for item in doc.colors() if not item.named and item.hex == "#781EA0"]
    assert len(purple) == 1 and purple[0].in_use and purple[0].uses == 1

    target = LIVE_KIT / "unnamed - Colorroom A.indd"
    report = indesign_win.apply(variant, {"#781EA0": "#EC4899"}, target)
    assert report.ok, report.summary()
    assert report.changed[0].exact and "unnamed" in report.changed[0].note
    after = indesign_win.read(target)
    assert [item.hex for item in after.colors() if not item.named and item.in_use] == ["#EC4899"]

    rewritten = idml.rewrite(variant, {"#781EA0": "#EC4899"}, LIVE_KIT / "unnamed - Colorroom D.idml")
    assert rewritten.ok and rewritten.changed[0].id == "Color/ucr1"
    opened = indesign_win.read(rewritten.saved)
    assert [item.hex for item in opened.colors() if not item.named and item.in_use] == ["#EC4899"]


@pytest.mark.indesign
@live
def test_live_user_documents_are_left_alone(kit):
    copy = LIVE_KIT / "open by the user.indd"
    shutil.copy2(kit["indd"], copy)
    indesign_win.show(copy)  # as if the user had opened it
    try:
        assert "open by the user.indd" in user_settings()["docs"]
        doc = indesign_win.read(copy)
        assert not doc.opened_by_colorroom
        active = indesign_win.active_document()
        assert active is not None and active["name"] == "open by the user.indd" and active["saved"]
        indesign_win.preview(None, LIVE_KIT / "active.png")
        assert "open by the user.indd" in user_settings()["docs"]  # still open, not closed by Colorroom
        report = indesign_win.apply(copy, {"#1D4ED8": "#000000"}, LIVE_KIT / "open by the user - Colorroom A.indd")
        assert report.ok and "open by the user.indd" in user_settings()["docs"]
        assert indesign_win.read(copy).swatch("CR RGB Blue").hex == "#1D4ED8"  # the open one is unchanged
    finally:
        closer = LIVE_KIT / "close.jsx"
        closer.write_text(
            '//@include "indesign_lib.jsx"\nvar d = CR.findOpen(CR_ARGS.path); if (d) { d.close(SaveOptions.NO); } CR.json({ok: true});\n', encoding="utf-8"
        )
        indesign_win.run_script(closer, {"path": indesign_win._js_path(copy)})


def test_declarations_are_refused_in_any_encoding():
    """The XML guard is made by the reader, so UTF-16 cannot hide an entity bomb (security
    review of 2026-11-14; written by Opus 5.5)."""
    bomb = '<?xml version="1.0" encoding="ENC"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;">]><x>&b;</x>'
    for encoding in ("utf-8", "utf-16", "utf-16-le", "utf-16-be"):
        with pytest.raises(idml.IdmlError):
            idml._parse_xml(bomb.replace("ENC", encoding.upper()).encode(encoding), "Graphic.xml")
    assert idml._parse_xml(b'<?xml version="1.0" encoding="UTF-8"?><a><b/></a>', "ok.xml").tag == "a"
