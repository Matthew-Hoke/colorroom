// Made by Opus 5.5. Colorroom, InDesign bridge: shared helpers.
//
// Every other indesign_*.jsx script includes this file on its first lines, so it can be run on
// its own from the Scripts panel. When Colorroom sends a script (base.compose_script), the
// include line is replaced by this file and a line  var CR_ARGS = {...};  comes first.
//
// ExtendScript is old JavaScript (ES3): no JSON, no Array.indexOf, no let or const.

var CR = {};

// ---- JSON out ----------------------------------------------------------------------------

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
    if (value instanceof Array) {
        out = [];
        for (i = 0; i < value.length; i++) {
            out.push(CR.json(value[i]));
        }
        return "[" + out.join(",") + "]";
    }
    out = [];
    for (key in value) {
        if (value.hasOwnProperty(key)) {
            out.push(CR.quote(key) + ":" + CR.json(value[key]));
        }
    }
    return "{" + out.join(",") + "}";
};

// ---- Small helpers -------------------------------------------------------------------------

CR.enumName = function (value) {
    // "PROCESS", "RGB", "PRINT_INTENT"... or "" when there is nothing to name.
    try {
        return value === null || value === undefined ? "" : String(value.toString());
    } catch (e) {
        return "";
    }
};

CR.get = function (object, name, fallback) {
    try {
        var value = object[name];
        return value === undefined ? fallback : value;
    } catch (e) {
        return fallback;
    }
};

CR.swatchKey = function (swatch) {
    // The id of a swatch-like object (Color, Tint, Gradient, MixedInk, Swatch), or null.
    try {
        if (swatch && typeof swatch === "object" && swatch.isValid) {
            return String(swatch.id);
        }
    } catch (e) {}
    return null;
};

CR.sameFile = function (a, b) {
    try {
        return File(a).fsName.toLowerCase() === File(b).fsName.toLowerCase();
    } catch (e) {
        return false;
    }
};

CR.hex = function (rgb) {
    var out = "#", i, v, h;
    if (!rgb || rgb.length !== 3) {
        return null;
    }
    for (i = 0; i < 3; i++) {
        v = Math.max(0, Math.min(255, Math.round(rgb[i])));
        h = v.toString(16).toUpperCase();
        out += h.length < 2 ? "0" + h : h;
    }
    return out;
};

CR.values = function (list) {
    var out = [], i;
    for (i = 0; list && i < list.length; i++) {
        out.push(Number(list[i]));
    }
    return out;
};

// ---- Quiet mode: no dialogs, points, no redraw; restored afterwards -----------------------

CR.saved = null;

CR.quiet = function () {
    var p = app.scriptPreferences;
    CR.saved = {interaction: p.userInteractionLevel, units: p.measurementUnit, redraw: p.enableRedraw};
    p.userInteractionLevel = UserInteractionLevels.NEVER_INTERACT;
    p.measurementUnit = MeasurementUnits.POINTS;
    p.enableRedraw = false;
};

CR.restore = function () {
    var p = app.scriptPreferences;
    if (!CR.saved) {
        return;
    }
    try { p.userInteractionLevel = CR.saved.interaction; } catch (e) {}
    try { p.measurementUnit = CR.saved.units; } catch (e) {}
    try { p.enableRedraw = CR.saved.redraw; } catch (e) {}
    CR.saved = null;
};

// ---- Documents -----------------------------------------------------------------------------

CR.opened = []; // documents this script opened; only these are ever closed

CR.findOpen = function (path) {
    var i, doc;
    for (i = 0; i < app.documents.length; i++) {
        doc = app.documents[i];
        try {
            if (doc.saved && CR.sameFile(doc.fullName, path)) {
                return doc;
            }
        } catch (e) {}
    }
    return null;
};

CR.document = function (path) {
    // An open document for this file, or the file opened without a window. With no path: the
    // active document. Opening never shows the missing-font or missing-link dialogs (quiet mode).
    var doc, file;
    if (!path) {
        if (app.documents.length === 0) {
            throw new Error("NO_DOCUMENT: InDesign has no document open.");
        }
        return {doc: app.activeDocument, ours: false};
    }
    doc = CR.findOpen(path);
    if (doc) {
        return {doc: doc, ours: false};
    }
    file = File(path);
    if (!file.exists) {
        throw new Error("NO_FILE: The file could not be found.");
    }
    doc = app.open(file, false);
    if (doc instanceof Array) {
        doc = doc[0];
    }
    CR.opened.push(doc);
    return {doc: doc, ours: true};
};

CR.closeOurs = function () {
    var i;
    for (i = CR.opened.length - 1; i >= 0; i--) {
        try {
            if (CR.opened[i].isValid) {
                CR.opened[i].close(SaveOptions.NO);
            }
        } catch (e) {}
    }
    CR.opened = [];
};

// ---- Color conversion in a hidden scratch document -----------------------------------------
// Converting a color inside the user's own document would add to its undo history and mark it
// changed, so conversions happen in a hidden new document with the same color profiles.

CR.scratch = null;

CR.openScratch = function (doc) {
    var s = app.documents.add(false);
    CR.opened.push(s);
    try { s.rgbProfile = doc.rgbProfile; } catch (e) {}
    try { s.cmykProfile = doc.cmykProfile; } catch (e) {}
    CR.scratch = s;
    return s;
};

CR.toRGB = function (spaceName, values) {
    // InDesign's own RGB for a color value, using the scratch document's profiles.
    var space, color, out;
    if (spaceName === "RGB") {
        return CR.values(values);
    }
    space = {CMYK: ColorSpace.CMYK, LAB: ColorSpace.LAB}[spaceName];
    if (!space || !CR.scratch) {
        return null;
    }
    color = CR.scratch.colors.add({model: ColorModel.PROCESS, space: space, colorValue: values});
    try {
        color.space = ColorSpace.RGB;
        out = CR.values(color.colorValue);
    } finally {
        color.remove();
    }
    return out;
};

// ---- Running a job -------------------------------------------------------------------------

CR.run = function (main) {
    var args = typeof CR_ARGS === "undefined" ? {} : CR_ARGS;
    var result;
    try {
        CR.quiet();
        result = main(args);
        result.ok = true;
    } catch (e) {
        result = {ok: false, error: String(e.message || e), line: CR.get(e, "line", null)};
    } finally {
        try { CR.closeOurs(); } catch (e2) {}
        CR.restore();
    }
    return CR.json(result);
};
