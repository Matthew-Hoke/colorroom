"""Write small PDF files for the tests, by hand, so no extra tool is needed.

Each page is a list of rectangles: (red, green, blue, x, y, width, height) with colors
0..1 in the PDF's RGB, or ("cmyk", c, m, y, k, x, y, width, height). Rectangles are real
vector shapes, so the file has exact fill colors to read back.
"""

from __future__ import annotations

from pathlib import Path


def build_pdf(pages, size=(300, 200), labels=False) -> bytes:
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    catalog = add(b"")  # filled in below
    tree = add(b"")
    kids = []
    for shapes in pages:
        parts = []
        for shape in shapes:
            if shape[0] == "cmyk":
                _, c, m, y, k, x, top, width, height = shape
                parts.append(f"{c} {m} {y} {k} k {x} {top} {width} {height} re f")
            elif shape[0] == "stroke":
                _, red, green, blue, x, top, width, height = shape
                parts.append(f"{red} {green} {blue} RG 4 w {x} {top} {width} {height} re S")
            else:
                red, green, blue, x, top, width, height = shape
                parts.append(f"{red} {green} {blue} rg {x} {top} {width} {height} re f")
        stream = "\n".join(parts).encode("ascii")
        content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        page = add(
            b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %d %d] /Contents %d 0 R /Resources << >> >>"
            % (tree, size[0], size[1], content)
        )
        kids.append(page)
    objects[tree - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % kid for kid in kids),
        len(kids),
    )
    extra = b""
    if labels:  # name the pages A, B, C... the way a designer might name artboards
        extra = b" /PageLabels << /Nums [0 << /S /A >>] >>"
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R%s >>" % (tree, extra)

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, catalog, start)
    return bytes(out)


def write_pdf(path, pages, **options) -> Path:
    target = Path(path)
    target.write_bytes(build_pdf(pages, **options))
    return target


#: A four-page "brand guide": white pages with a navy and an orange block; page 3 uses a
#: slightly different navy (a typical inconsistency), page 4 adds a green that appears nowhere else.
BRAND_GUIDE = [
    [(1, 1, 1, 0, 0, 300, 200), (0.063, 0.165, 0.329, 20, 20, 120, 160), (0.878, 0.271, 0.184, 160, 20, 120, 160)],
    [(1, 1, 1, 0, 0, 300, 200), (0.063, 0.165, 0.329, 20, 20, 260, 70), (0.878, 0.271, 0.184, 20, 110, 260, 70)],
    [(1, 1, 1, 0, 0, 300, 200), (0.094, 0.196, 0.376, 20, 20, 260, 160)],
    [(1, 1, 1, 0, 0, 300, 200), (0.063, 0.165, 0.329, 20, 20, 120, 160), (0.18, 0.545, 0.341, 160, 20, 120, 160)],
]
