// Made by Fable 5.2. Colorroom, Illustrator bridge: shared helpers.
//
// Every other illustrator_*.jsx script starts with  //@include "illustrator_lib.jsx"  so it can
// be run on its own from File > Scripts. When Colorroom sends a script it puts this file in
// front of it instead, with a line  var CR_ARGS = {...};  that holds the arguments.
//
// ExtendScript is old JavaScript (ES3): no JSON, no Array.indexOf, no let or const.

var CR = {};

// ---- JSON out -------------------------------------------------------------------------

CR.quote = function (text) {
    var out = '"', i, ch, code, hex;
    text = String(text);
    for (i = 0; i < text.length; i++) {
        ch = text.charAt(i);
        code = text.charCodeAt(i);
        if (ch === '"') {
            out += '\\"';
        } else if (ch === "\\") {
            out += "\\\\";
        } else if (code < 32 || code > 126) {
            hex = code.toString(16);
            out += "\\u" + "0000".substr(hex.length) + hex;
        } else {
            out += ch;
        }
    }
    return out + '"';
};

CR.json = function (value) {
    var kind = typeof value, out, i, key;
    if (value === null || value === undefined) {
        return "null";
    }
    if (kind === "number") {
        return isFinite(value) ? String(Math.round(value * 10000) / 10000) : "null";
    }
    if (kind === "boolean") {
        return value ? "true" : "false";
    }
    if (kind === "string") {
        return CR.quote(value);
    }
    out = [];
    if (value instanceof Array) {
        for (i = 0; i < value.length; i++) {
            out.push(CR.json(value[i]));
        }
        return "[" + out.join(",") + "]";
    }
    for (key in value) {
        if (value.hasOwnProperty(key)) {
            out.push(CR.quote(key) + ":" + CR.json(value[key]));
        }
    }
    return "{" + out.join(",") + "}";
};

// Runs one job with Illustrator's own dialogs switched off, and always answers with JSON text.
CR.run = function (job) {
    var before = app.userInteractionLevel, answer;
    app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;
    try {
        answer = job(typeof CR_ARGS === "undefined" ? {} : CR_ARGS);
        answer.ok = true;
    } catch (error) {
        answer = { ok: false, error: String(error.message || error), line: error.line || 0 };
    }
    try {
        app.userInteractionLevel = before;
    } catch (ignore) {}
    return CR.json(answer);
};

// ---- documents ------------------------------------------------------------------------

CR.samePath = function (a, b) {
    return String(a).replace(/\\/g, "/").toLowerCase() === String(b).replace(/\\/g, "/").toLowerCase();
};

CR.docPath = function (doc) {
    // An untitled document has no file yet.
    try {
        var file = doc.fullName;
        return file && file.exists ? file.fsName : "";
    } catch (error) {
        return "";
    }
};

CR.findDocument = function (path) {
    var i, wanted;
    if (!path) {
        if (app.documents.length === 0) {
            throw new Error("No document is open in Illustrator.");
        }
        return app.activeDocument;
    }
    wanted = new File(path).fsName;
    for (i = 0; i < app.documents.length; i++) {
        if (CR.samePath(CR.docPath(app.documents[i]), wanted)) {
            return app.documents[i];
        }
    }
    return null;
};

CR.documentInfo = function (doc) {
    var boards = [], i, rect;
    for (i = 0; i < doc.artboards.length; i++) {
        rect = doc.artboards[i].artboardRect; // left, top, right, bottom (y grows upward)
        boards.push({
            name: doc.artboards[i].name,
            rect: [rect[0], rect[1], rect[2], rect[3]],
            width: rect[2] - rect[0],
            height: rect[1] - rect[3]
        });
    }
    return {
        name: doc.name,
        path: CR.docPath(doc),
        colorMode: doc.documentColorSpace === DocumentColorSpace.CMYK ? "CMYK" : "RGB",
        width: doc.width,
        height: doc.height,
        saved: doc.saved,
        artboards: boards
    };
};

// ---- colors ---------------------------------------------------------------------------

CR.hex2 = function (number) {
    var text = Math.max(0, Math.min(255, Math.round(number))).toString(16).toUpperCase();
    return text.length < 2 ? "0" + text : text;
};

CR.hex = function (rgb) {
    return "#" + CR.hex2(rgb[0]) + CR.hex2(rgb[1]) + CR.hex2(rgb[2]);
};

CR.hexToRgb = function (hex) {
    hex = String(hex).replace("#", "");
    return [parseInt(hex.substr(0, 2), 16), parseInt(hex.substr(2, 2), 16), parseInt(hex.substr(4, 2), 16)];
};

// Illustrator's own conversion, through the document's color settings.
CR.convert = function (from, values, to) {
    try {
        return app.convertSampleColor(from, values, to, ColorConvertPurpose.defaultpurpose);
    } catch (error) {
        return null;
    }
};

// A plain description of any Illustrator color. null means "no color".
CR.color = function (color) {
    var kind, values, rgb, base, spot;
    if (!color) {
        return null;
    }
    kind = color.typename;
    if (kind === "NoColor") {
        return null;
    }
    if (kind === "RGBColor") {
        values = [color.red, color.green, color.blue];
        return { type: "rgb", values: values, hex: CR.hex(values) };
    }
    if (kind === "CMYKColor") {
        values = [color.cyan, color.magenta, color.yellow, color.black];
        rgb = CR.convert(ImageColorSpace.CMYK, values, ImageColorSpace.RGB);
        return { type: "cmyk", values: values, hex: rgb ? CR.hex(rgb) : null };
    }
    if (kind === "GrayColor") {
        values = [color.gray]; // 0 is white, 100 is black
        rgb = CR.convert(ImageColorSpace.GrayScale, values, ImageColorSpace.RGB);
        if (!rgb) {
            rgb = [255 * (1 - color.gray / 100), 255 * (1 - color.gray / 100), 255 * (1 - color.gray / 100)];
        }
        return { type: "gray", values: values, hex: CR.hex(rgb) };
    }
    if (kind === "LabColor") {
        values = [color.l, color.a, color.b];
        rgb = CR.convert(ImageColorSpace.LAB, values, ImageColorSpace.RGB);
        return { type: "lab", values: values, hex: rgb ? CR.hex(rgb) : null };
    }
    if (kind === "SpotColor") {
        spot = color.spot;
        base = CR.color(spot.color);
        return {
            type: CR.spotKind(spot),
            swatch: spot.name,
            tint: color.tint,
            hex: base ? base.hex : null,
            base: base
        };
    }
    if (kind === "GradientColor") {
        return { type: "gradient", gradient: color.gradient.name };
    }
    if (kind === "PatternColor") {
        return { type: "pattern", pattern: color.pattern.name };
    }
    return { type: "other", note: kind };
};

CR.spotKind = function (spot) {
    if (spot.colorType === ColorModel.PROCESS) {
        return "global";
    }
    if (spot.colorType === ColorModel.REGISTRATION) {
        return "registration";
    }
    return "spot";
};

CR.isPlain = function (described) {
    return !!described && (described.type === "rgb" || described.type === "cmyk" || described.type === "gray" || described.type === "lab");
};

// What a new hex becomes in this document: the same RGB numbers in an RGB document,
// Illustrator's own conversion in a CMYK document.
CR.target = function (doc, hex) {
    var rgb = CR.hexToRgb(hex), cmyk, back;
    if (doc.documentColorSpace !== DocumentColorSpace.CMYK) {
        return { type: "rgb", values: rgb, hex: CR.hex(rgb), asked: hex };
    }
    cmyk = CR.convert(ImageColorSpace.RGB, rgb, ImageColorSpace.CMYK);
    if (!cmyk) {
        throw new Error("Illustrator could not convert " + hex + " to CMYK.");
    }
    back = CR.convert(ImageColorSpace.CMYK, cmyk, ImageColorSpace.RGB);
    return { type: "cmyk", values: [cmyk[0], cmyk[1], cmyk[2], cmyk[3]], hex: back ? CR.hex(back) : null, asked: hex, converted: true };
};

CR.make = function (target) {
    var color;
    if (target.type === "cmyk") {
        color = new CMYKColor();
        color.cyan = target.values[0];
        color.magenta = target.values[1];
        color.yellow = target.values[2];
        color.black = target.values[3];
    } else {
        color = new RGBColor();
        color.red = target.values[0];
        color.green = target.values[1];
        color.blue = target.values[2];
    }
    return color;
};

// ---- walking the document -------------------------------------------------------------

// Calls visit.layer(layer, id, path, locked, hidden) and visit.item(item, id, path, locked,
// hidden) for everything, top-down. "locked" and "hidden" include every parent. Ids are the
// position in the document ("L0.L1/3.0": layer 0, its sublayer 1, item 3, its child 0), so
// they stay the same as long as nothing is added, removed or reordered.
CR.walk = function (doc, visit) {
    function walkItem(item, id, path, locked, hidden) {
        var isLocked = locked || item.locked, isHidden = hidden || item.hidden, i, children;
        if (visit.item) {
            visit.item(item, id, path, isLocked, isHidden);
        }
        if (item.typename === "GroupItem") {
            children = item.pageItems;
            for (i = 0; i < children.length; i++) {
                walkItem(children[i], id + "." + i, path, isLocked, isHidden);
            }
        }
    }
    function walkLayer(layer, id, path, locked, hidden) {
        var isLocked = locked || layer.locked, isHidden = hidden || !layer.visible, i, items, subs;
        if (visit.layer) {
            visit.layer(layer, id, path, isLocked, isHidden);
        }
        items = layer.pageItems;
        for (i = 0; i < items.length; i++) {
            walkItem(items[i], id + "/" + i, path, isLocked, isHidden);
        }
        subs = layer.layers;
        for (i = 0; i < subs.length; i++) {
            walkLayer(subs[i], id + ".L" + i, path + " / " + subs[i].name, isLocked, isHidden);
        }
    }
    var n;
    for (n = 0; n < doc.layers.length; n++) {
        walkLayer(doc.layers[n], "L" + n, doc.layers[n].name, false, false);
    }
};

CR.kinds = {
    PathItem: "path",
    CompoundPathItem: "compound",
    GroupItem: "group",
    TextFrame: "text",
    PlacedItem: "placed",
    RasterItem: "raster",
    MeshItem: "mesh",
    SymbolItem: "symbol",
    PluginItem: "plugin",
    GraphItem: "graph",
    LegacyTextItem: "legacy-text",
    NonNativeItem: "non-native"
};

// The path that carries a compound path's look (all its parts share one fill and stroke).
CR.paintOf = function (item) {
    if (item.typename === "PathItem") {
        return item;
    }
    if (item.typename === "CompoundPathItem" && item.pathItems.length > 0) {
        return item.pathItems[0];
    }
    return null;
};

CR.runKey = function (described) {
    if (!described) {
        return "-";
    }
    return [described.type, described.hex, described.swatch, described.tint, described.gradient, described.pattern,
        described.values ? described.values.join(",") : ""].join(":");
};

// The colors of a text frame as runs: {start, length, fill, stroke}. Neighbours with the same
// colors are joined, so a run is "these characters share one fill and one stroke".
CR.runs = function (frame) {
    var runs = [], total, ranges, i, range, length, position = 0, last = null, attributes, fill, stroke, key, perCharacter = false;
    try {
        total = frame.characters.length;
    } catch (error) {
        return runs;
    }
    if (total === 0) {
        return runs;
    }
    function add(attributesOf, size) {
        try {
            fill = CR.color(attributesOf.fillColor);
        } catch (e1) {
            fill = { type: "other", note: "unreadable" };
        }
        try {
            stroke = CR.color(attributesOf.strokeColor);
        } catch (e2) {
            stroke = null;
        }
        key = CR.runKey(fill) + "|" + CR.runKey(stroke);
        if (last && last.key === key) {
            last.length += size;
        } else {
            last = { start: position, length: size, fill: fill, stroke: stroke, key: key };
            runs.push(last);
        }
        position += size;
    }
    // textRanges are Illustrator's own style runs; check that they add up before trusting them.
    try {
        ranges = frame.textRanges;
        for (i = 0; i < ranges.length; i++) {
            range = ranges[i];
            length = range.length;
            add(range.characterAttributes, length);
        }
        if (position !== total) {
            perCharacter = true;
        }
    } catch (error2) {
        perCharacter = true;
    }
    if (perCharacter) {
        runs = [];
        last = null;
        position = 0;
        for (i = 0; i < total; i++) {
            add(frame.characters[i].characterAttributes, 1);
        }
    }
    for (i = 0; i < runs.length; i++) {
        delete runs[i].key;
    }
    return runs;
};

CR.describeItem = function (item, id, path, locked, hidden) {
    var kind = CR.kinds[item.typename] || "other", paint, bounds,
        out = { id: id, kind: kind, name: item.name, layer: path, locked: locked, hidden: hidden };
    try {
        bounds = item.geometricBounds; // left, top, right, bottom
        out.bounds = [bounds[0], bounds[1], bounds[2], bounds[3]];
    } catch (error) {}
    if (kind === "other") {
        out.typename = item.typename;
    }
    if (kind === "group") {
        if (item.clipped) {
            out.kind = "clip-group";
        }
        out.children = item.pageItems.length;
    }
    if (kind === "compound") {
        out.paths = item.pathItems.length;
        if (out.paths === 0) {
            out.note = "This compound path keeps its shapes where scripts cannot reach them.";
        }
    }
    if (kind === "path" || kind === "compound") {
        paint = CR.paintOf(item);
        if (paint) {
            if (kind === "path" && paint.guides) {
                out.kind = "guide";
            } else {
                if (kind === "path" && paint.clipping) {
                    out.clipping = true;
                }
                out.fill = paint.filled ? CR.color(paint.fillColor) : null;
                out.stroke = paint.stroked ? CR.color(paint.strokeColor) : null;
                if (paint.stroked) {
                    out.strokeWidth = paint.strokeWidth;
                }
            }
        }
    }
    if (kind === "text") {
        out.runs = CR.runs(item);
        out.fonts = CR.fonts(item);
        out.missingFonts = [];
        for (var f = 0; f < out.fonts.length; f++) {
            if (!CR.fontInstalled(out.fonts[f])) {
                out.missingFonts.push(out.fonts[f]);
            }
        }
        try {
            out.chars = item.characters.length;
            out.text = String(item.contents).substr(0, 60);
        } catch (error2) {}
    }
    return out;
};

// The fonts a text frame uses, by PostScript name. "?" when Illustrator cannot say.
CR.fonts = function (frame) {
    var names = [], seen = {}, ranges, i, name;
    try {
        ranges = frame.textRanges;
        for (i = 0; i < ranges.length; i++) {
            try {
                name = ranges[i].characterAttributes.textFont.name;
            } catch (error) {
                name = "?";
            }
            if (!seen[name]) {
                seen[name] = true;
                names.push(name);
            }
        }
    } catch (error2) {}
    return names;
};

CR.fontCache = {};
CR.fontInstalled = function (name) {
    if (CR.fontCache.hasOwnProperty(name)) {
        return CR.fontCache[name];
    }
    var found = false;
    if (name !== "?") {
        try {
            found = app.textFonts.getByName(name).name === name;
        } catch (error) {
            found = false;
        }
    }
    CR.fontCache[name] = found;
    return found;
};

CR.describeGradient = function (gradient) {
    var stops = [], i, stop;
    for (i = 0; i < gradient.gradientStops.length; i++) {
        stop = gradient.gradientStops[i];
        stops.push({ ramp: stop.rampPoint, mid: stop.midPoint, opacity: stop.opacity, color: CR.color(stop.color) });
    }
    return { name: gradient.name, type: gradient.type === GradientType.RADIAL ? "radial" : "linear", stops: stops };
};

// Everything Colorroom needs to know about a document, as one plain object.
CR.snapshot = function (doc) {
    var out = { document: CR.documentInfo(doc), layers: [], items: [], gradients: [], spots: [], swatches: [], counts: {}, notes: [] },
        listed = {}, i, spot, swatch, described, names;

    for (i = 0; i < doc.gradients.length; i++) {
        described = CR.describeGradient(doc.gradients[i]);
        listed[described.name] = true;
        out.gradients.push(described);
    }
    for (i = 0; i < doc.spots.length; i++) {
        spot = doc.spots[i];
        out.spots.push({ name: spot.name, kind: CR.spotKind(spot), color: CR.color(spot.color) });
    }
    for (i = 0; i < doc.swatches.length; i++) {
        swatch = doc.swatches[i];
        try {
            out.swatches.push({ name: swatch.name, color: CR.color(swatch.color) });
        } catch (error) {
            out.swatches.push({ name: swatch.name, color: { type: "other", note: "unreadable" } });
        }
    }

    function noteGradient(color, id) {
        // A gradient that is on an object but not in the document's gradient list.
        var found;
        if (color && color.typename === "GradientColor" && !listed[color.gradient.name]) {
            found = CR.describeGradient(color.gradient);
            found.viaItem = id;
            listed[found.name] = true;
            out.gradients.push(found);
        }
    }

    CR.walk(doc, {
        layer: function (layer, id, path, locked, hidden) {
            out.layers.push({
                id: id, name: layer.name, path: path, visible: layer.visible, locked: layer.locked,
                lockedWithParents: locked, hiddenWithParents: hidden, printable: layer.printable,
                items: layer.pageItems.length, sublayers: layer.layers.length
            });
        },
        item: function (item, id, path, locked, hidden) {
            var paint = CR.paintOf(item);
            out.items.push(CR.describeItem(item, id, path, locked, hidden));
            if (paint) {
                try {
                    if (paint.filled) {
                        noteGradient(paint.fillColor, id);
                    }
                    if (paint.stroked) {
                        noteGradient(paint.strokeColor, id);
                    }
                } catch (error) {}
            }
        }
    });

    names = ["layers", "artboards", "pageItems", "pathItems", "compoundPathItems", "groupItems", "textFrames", "placedItems",
        "rasterItems", "meshItems", "symbolItems", "pluginItems", "graphItems", "legacyTextItems", "nonNativeItems",
        "gradients", "spots", "swatches", "symbols", "stories"];
    for (i = 0; i < names.length; i++) {
        try {
            out.counts[names[i]] = doc[names[i]].length;
        } catch (error2) {
            out.counts[names[i]] = null;
        }
    }
    return out;
};
