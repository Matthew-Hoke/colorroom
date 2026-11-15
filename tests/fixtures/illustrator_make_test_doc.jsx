// Made by Opus 5.5 (continuing Fable 5.2's start). Colorroom test kit: builds an Illustrator
// document with every kind of color the Illustrator bridge handles, and saves it as .ai.
//
// Arguments (CR_ARGS): path (where to save, .ai), mode ("RGB" or "CMYK"), image (optional PNG
// to place, linked). The tests send this file with illustrator_lib.jsx in front of it.
//@include "../../src/colorroom/core/bridges/scripts/illustrator_lib.jsx"

CR.run(function (args) {
    var doc = app.documents.add(args.mode === "CMYK" ? DocumentColorSpace.CMYK : DocumentColorSpace.RGB, 800, 600),
        before = app.coordinateSystem;
    try {
        return buildTestDocument(doc, args);
    } catch (error) {
        app.coordinateSystem = before;
        doc.close(SaveOptions.DONOTSAVECHANGES); // never leave a half-built document behind
        throw error;
    }
});

function buildTestDocument(doc, args) {
    var cmyk = args.mode === "CMYK",
        board, left, top, back, artwork, details, item, group, part, ring, clip, text, i, spotGlobal, spotOrange,
        gradient, gradientColor, before = app.coordinateSystem;

    app.coordinateSystem = CoordinateSystem.DOCUMENTCOORDINATESYSTEM;

    function color(hex) {
        var rgb = CR.hexToRgb(hex), out, values;
        if (!cmyk) {
            out = new RGBColor();
            out.red = rgb[0];
            out.green = rgb[1];
            out.blue = rgb[2];
            return out;
        }
        values = CR.convert(ImageColorSpace.RGB, rgb, ImageColorSpace.CMYK);
        out = new CMYKColor();
        out.cyan = values[0];
        out.magenta = values[1];
        out.yellow = values[2];
        out.black = values[3];
        return out;
    }
    function swatchColor(spot) {
        var out = new SpotColor();
        out.spot = spot;
        out.tint = 100;
        return out;
    }
    function rect(layer, name, x, y, w, h, fill, stroke, width) {
        var shape = layer.pathItems.rectangle(top - y, left + x, w, h);
        shape.name = name;
        paint(shape, fill, stroke, width);
        return shape;
    }
    function paint(shape, fill, stroke, width) {
        if (fill) {
            shape.filled = true;
            shape.fillColor = typeof fill === "string" ? color(fill) : fill;
        } else {
            shape.filled = false;
        }
        if (stroke) {
            shape.stroked = true;
            shape.strokeColor = typeof stroke === "string" ? color(stroke) : stroke;
            shape.strokeWidth = width || 2;
        } else {
            shape.stroked = false;
        }
    }

    board = doc.artboards[0];
    board.name = "Poster";
    left = board.artboardRect[0];
    top = board.artboardRect[1];
    doc.artboards.add([left + 900, top, left + 1300, top - 300]);
    doc.artboards[1].name = "Card";

    // Layers, bottom to top: Background, Artwork (with the sublayer Details).
    back = doc.layers[0];
    back.name = "Background";
    artwork = doc.layers.add();
    artwork.name = "Artwork";
    details = artwork.layers.add();
    details.name = "Details";

    rect(back, "Paper", 0, 0, 800, 600, "#F5F0E6", null);

    // A plain rectangle with fill and stroke.
    rect(artwork, "Card", 40, 40, 300, 180, "#1D4ED8", "#111827", 4);

    // A compound path: a ring made of two circles.
    ring = artwork.compoundPathItems.add();
    ring.name = "Ring";
    part = ring.pathItems.ellipse(top - 60, left + 400, 140, 140);
    part = ring.pathItems.ellipse(top - 100, left + 440, 60, 60);
    for (i = 0; i < ring.pathItems.length; i++) {
        paint(ring.pathItems[i], "#E11D48", null);
    }

    // A group with the same blue as the card (one color, three users).
    group = artwork.groupItems.add();
    group.name = "Badge";
    item = group.pathItems.rectangle(top - 60, left + 600, 140, 60);
    item.name = "Badge back";
    paint(item, "#F59E0B", null);
    item = group.pathItems.ellipse(top - 70, left + 610, 40, 40);
    item.name = "Badge dot";
    paint(item, "#1D4ED8", null);

    // A clipping group: a green star seen through a rectangle.
    clip = artwork.groupItems.add();
    clip.name = "Clipped star";
    item = clip.pathItems.star(left + 120, top - 330, 70, 30, 5);
    item.name = "Star";
    paint(item, "#10B981", null);
    item = clip.pathItems.rectangle(top - 270, left + 60, 120, 90);
    item.name = "Mask";
    item.move(clip, ElementPlacement.PLACEATBEGINNING);
    clip.clipped = true;
    clip.pageItems[0].clipping = true;

    // Text with two colors.
    text = artwork.textFrames.add();
    text.name = "Headline";
    text.contents = "Hello World";
    text.position = [left + 40, top - 480];
    text.textRange.characterAttributes.size = 48;
    for (i = 0; i < text.characters.length; i++) {
        text.characters[i].characterAttributes.fillColor = color(i < 6 ? "#111827" : "#E11D48");
    }

    // A linear gradient.
    gradient = doc.gradients.add();
    gradient.name = "Test Sky";
    gradient.type = GradientType.LINEAR;
    gradient.gradientStops[0].color = color("#0EA5E9");
    gradient.gradientStops[1].color = color("#8B5CF6");
    gradientColor = new GradientColor();
    gradientColor.gradient = gradient;
    item = rect(artwork, "Sky", 400, 260, 360, 120, gradientColor, null);

    // A global process swatch used by two objects.
    spotGlobal = doc.spots.add();
    spotGlobal.name = "Brand Green";
    spotGlobal.colorType = ColorModel.PROCESS;
    spotGlobal.color = color("#16A34A");
    rect(artwork, "Global A", 240, 270, 100, 60, swatchColor(spotGlobal), null);
    item = artwork.pathItems.ellipse(top - 270, left + 360, 30, 30);
    item.name = "Global B";
    paint(item, swatchColor(spotGlobal), null);

    // A spot color.
    spotOrange = doc.spots.add();
    spotOrange.name = "Spot Orange";
    spotOrange.colorType = ColorModel.SPOT;
    spotOrange.color = color("#F97316");
    rect(artwork, "Spot rect", 240, 360, 100, 60, swatchColor(spotOrange), null);

    // A locked object and a hidden object, both using the card blue.
    item = rect(artwork, "Locked box", 600, 140, 60, 60, "#1D4ED8", null);
    item.locked = true;
    item = rect(artwork, "Hidden box", 680, 140, 60, 60, "#1D4ED8", null);
    item.hidden = true;

    // The sublayer.
    rect(details, "Detail", 40, 240, 160, 20, "#E11D48", "#1D4ED8", 2);

    // A locked sublayer with one object in it.
    var frozen = artwork.layers.add();
    frozen.name = "Frozen";
    rect(frozen, "Frozen box", 680, 220, 60, 30, "#1D4ED8", null);

    // The global swatch again, at a 50% tint.
    var tinted = swatchColor(spotGlobal);
    tinted.tint = 50;
    rect(artwork, "Global tint", 360, 320, 30, 30, tinted, null);

    // The second artboard.
    rect(artwork, "Card art", 920, 20, 360, 260, "#F59E0B", "#111827", 3);

    // A placed picture, left linked.
    if (args.image) {
        item = back.placedItems.add();
        item.file = new File(args.image);
        item.name = "Photo";
        item.position = [left + 560, top - 420];
    }

    frozen.locked = true; // last: Illustrator refuses new objects while a layer in the way is locked
    app.coordinateSystem = before;
    var options = new IllustratorSaveOptions();
    options.pdfCompatible = true;
    doc.saveAs(new File(args.path), options);
    var info = CR.snapshot(doc);
    doc.close(SaveOptions.DONOTSAVECHANGES);
    return { snapshot: info };
}
