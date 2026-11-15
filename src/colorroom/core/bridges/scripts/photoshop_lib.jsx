// Made by Opus 5.5 (2026-11-14). Colorroom, Photoshop bridge (route A): shared helpers.
//
// Every other photoshop_*.jsx script starts with  //@include "photoshop_lib.jsx"  so it can be
// run on its own from File > Scripts > Browse. When Colorroom sends a script through COM it
// removes that line and puts this file in front of it instead, after a line
//     var CR_ARGS = {...};
// that holds the arguments. Each script ends with CR.run(...), whose JSON text is the result.
//
// ExtendScript is old JavaScript (ES3): no JSON, no Array.indexOf, no let or const.
//
// The core of this file is one walker over Action Manager descriptors. CR.scan finds every
// color object and names it by its path of keys ("fx/solidFill/color"); CR.recolor copies a
// descriptor and replaces the colors named in a mapping, recording the same paths. Reading
// and writing therefore agree on where every color is, and Colorroom can check each one.

var CR = {};

CR.s = function (name) {
    return stringIDToTypeID(name);
};
CR.c = function (code) {
    return charIDToTypeID(code);
};

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
    var parts = [], key, i;
    if (value === null || value === undefined) {
        return "null";
    }
    if (typeof value === "number") {
        return isFinite(value) ? String(value) : "null";
    }
    if (typeof value === "boolean") {
        return value ? "true" : "false";
    }
    if (typeof value === "string") {
        return CR.quote(value);
    }
    if (value instanceof Array) {
        for (i = 0; i < value.length; i++) {
            parts.push(CR.json(value[i]));
        }
        return "[" + parts.join(",") + "]";
    }
    for (key in value) {
        if (value.hasOwnProperty(key) && typeof value[key] !== "function") {
            parts.push(CR.quote(key) + ":" + CR.json(value[key]));
        }
    }
    return "{" + parts.join(",") + "}";
};

// ---- errors and the run wrapper ---------------------------------------------------------

// An error with a short code and a message written for the person using Colorroom.
CR.fail = function (code, message) {
    var error = new Error(message);
    error.crCode = code;
    return error;
};

// Runs job(args, state) with dialogs off, then puts Photoshop's settings back. The job may
// set state.active to the document that should be in front afterwards.
CR.run = function (job) {
    var args = typeof CR_ARGS === "undefined" ? {} : CR_ARGS;
    var state = { dialogs: app.displayDialogs, active: null, units: app.preferences.rulerUnits };
    var result;
    try {
        state.active = app.documents.length ? app.activeDocument : null;
    } catch (ignore) {}
    app.displayDialogs = DialogModes.NO;
    try {
        result = job(args, state);
        result.ok = true;
    } catch (e) {
        result = { ok: false, code: e.crCode || "script", message: String(e.message || e), line: e.line || 0 };
    }
    try {
        if (state.active) {
            app.activeDocument = state.active;
        }
    } catch (ignore2) {}
    try {
        app.preferences.rulerUnits = state.units;
    } catch (ignore3) {}
    app.displayDialogs = state.dialogs;
    return CR.json(result);
};

// ---- names ------------------------------------------------------------------------------

CR.name = function (id) {
    var text = "";
    try {
        text = typeIDToStringID(id);
    } catch (ignore) {}
    if (!text) {
        try {
            text = typeIDToCharID(id);
        } catch (ignore2) {}
    }
    return text || String(id);
};

// ---- colors -----------------------------------------------------------------------------

CR.MODEL = {};
CR.MODEL[CR.c("RGBC")] = "rgb";
CR.MODEL[CR.c("HSBC")] = "hsb";
CR.MODEL[CR.c("CMYC")] = "cmyk";
CR.MODEL[CR.c("LbCl")] = "lab";
CR.MODEL[CR.c("Grsc")] = "gray";
CR.MODEL[CR.c("BkCl")] = "book";

CR.isColor = function (classId) {
    return CR.MODEL.hasOwnProperty(classId);
};

CR.hex2 = function (value) {
    var text = Math.max(0, Math.min(255, Math.round(value))).toString(16).toUpperCase();
    return text.length < 2 ? "0" + text : text;
};

CR.number = function (desc, key) {
    var type = desc.getType(key);
    if (type === DescValueType.UNITDOUBLE) {
        return desc.getUnitDoubleValue(key);
    }
    if (type === DescValueType.INTEGERTYPE) {
        return desc.getInteger(key);
    }
    return desc.getDouble(key);
};

// The color as "#RRGGBB", or null when Colorroom cannot read it (a color book color).
// exact is false when Photoshop converted it from another model (CMYK, Lab, gray).
CR.hexOf = function (desc, classId) {
    var model = CR.MODEL[classId], color, red, green, blue;
    try {
        if (model === "rgb") {
            if (desc.hasKey(CR.c("Rd  "))) {
                red = CR.number(desc, CR.c("Rd  "));
                green = CR.number(desc, CR.c("Grn "));
                blue = CR.number(desc, CR.c("Bl  "));
            } else if (desc.hasKey(CR.s("redFloat"))) {
                red = CR.number(desc, CR.s("redFloat")) * 255;
                green = CR.number(desc, CR.s("greenFloat")) * 255;
                blue = CR.number(desc, CR.s("blueFloat")) * 255;
            } else {
                return null;
            }
            return { hex: "#" + CR.hex2(red) + CR.hex2(green) + CR.hex2(blue), exact: true };
        }
        color = new SolidColor();
        if (model === "hsb") {
            color.hsb.hue = CR.number(desc, CR.c("H   "));
            color.hsb.saturation = CR.number(desc, CR.c("Strt"));
            color.hsb.brightness = CR.number(desc, CR.c("Brgh"));
        } else if (model === "cmyk") {
            color.cmyk.cyan = CR.number(desc, CR.c("Cyn "));
            color.cmyk.magenta = CR.number(desc, CR.c("Mgnt"));
            color.cmyk.yellow = CR.number(desc, CR.c("Ylw "));
            color.cmyk.black = CR.number(desc, CR.c("Blck"));
        } else if (model === "lab") {
            color.lab.l = CR.number(desc, CR.c("Lmnc"));
            color.lab.a = CR.number(desc, CR.c("A   "));
            color.lab.b = CR.number(desc, CR.c("B   "));
        } else if (model === "gray") {
            color.gray.gray = CR.number(desc, CR.c("Gry "));
        } else {
            return null;
        }
        return { hex: "#" + String(color.rgb.hexValue).toUpperCase(), exact: model === "hsb" };
    } catch (e) {
        return null;
    }
};

CR.rgbDesc = function (hex) {
    var desc = new ActionDescriptor();
    desc.putDouble(CR.c("Rd  "), parseInt(hex.substr(1, 2), 16));
    desc.putDouble(CR.c("Grn "), parseInt(hex.substr(3, 2), 16));
    desc.putDouble(CR.c("Bl  "), parseInt(hex.substr(5, 2), 16));
    return desc;
};

CR.solid = function (hex) {
    var color = new SolidColor();
    color.rgb.hexValue = hex.substr(1);
    return color;
};

// ---- the walker: find colors --------------------------------------------------------------

// Appends {key, hex, exact, model, stop} to out for every color object inside desc.
// stop is {location (0 to 4096), type} for colors that are gradient stops.
CR.scan = function (desc, path, out, stop) {
    var i, key, type;
    for (i = 0; i < desc.count; i++) {
        key = desc.getKey(i);
        type = desc.getType(key);
        if (type === DescValueType.OBJECTTYPE) {
            CR.scanObject(desc.getObjectValue(key), desc.getObjectType(key), path + "/" + CR.name(key), out, stop);
        } else if (type === DescValueType.LISTTYPE) {
            CR.scanList(desc.getList(key), path + "/" + CR.name(key), out, stop);
        }
    }
};

CR.scanList = function (list, path, out, stop) {
    var i, type;
    for (i = 0; i < list.count; i++) {
        type = list.getType(i);
        if (type === DescValueType.OBJECTTYPE) {
            CR.scanObject(list.getObjectValue(i), list.getObjectType(i), path + "/" + i, out, stop);
        } else if (type === DescValueType.LISTTYPE) {
            CR.scanList(list.getList(i), path + "/" + i, out, stop);
        }
    }
};

CR.scanObject = function (obj, classId, path, out, stop) {
    var info, here = stop;
    if (CR.isColor(classId)) {
        info = CR.hexOf(obj, classId);
        out.push({ key: path, hex: info ? info.hex : null, exact: info ? info.exact : false, model: CR.MODEL[classId], stop: stop || null });
        return;
    }
    if (classId === CR.c("Clrt")) {
        here = { location: obj.hasKey(CR.c("Lctn")) ? CR.number(obj, CR.c("Lctn")) : null, type: obj.hasKey(CR.c("Type")) ? CR.name(obj.getEnumerationValue(CR.c("Type"))) : "" };
    }
    CR.scan(obj, path, out, here);
};

// ---- the walker: copy with new colors ------------------------------------------------------

// ctx = {map: {"#OLD": "#NEW"}, changes: [], accept: function (path) -> bool or null}.
// Returns a copy of desc; every accepted color found in map is replaced by its new RGB color
// and recorded in ctx.changes as {key, from, to}. Everything else is copied as it is.
CR.recolor = function (desc, path, ctx) {
    var out = new ActionDescriptor(), i;
    for (i = 0; i < desc.count; i++) {
        CR.copyKey(desc, desc.getKey(i), out, path, ctx);
    }
    return out;
};

CR.copyKey = function (src, key, dst, path, ctx) {
    var type = src.getType(key), here = path + "/" + CR.name(key), pair;
    if (type === DescValueType.OBJECTTYPE) {
        pair = CR.recolorObject(src.getObjectValue(key), src.getObjectType(key), here, ctx);
        dst.putObject(key, pair[0], pair[1]);
    } else if (type === DescValueType.LISTTYPE) {
        dst.putList(key, CR.recolorList(src.getList(key), here, ctx));
    } else if (type === DescValueType.BOOLEANTYPE) {
        dst.putBoolean(key, src.getBoolean(key));
    } else if (type === DescValueType.INTEGERTYPE) {
        dst.putInteger(key, src.getInteger(key));
    } else if (type === DescValueType.DOUBLETYPE) {
        dst.putDouble(key, src.getDouble(key));
    } else if (type === DescValueType.UNITDOUBLE) {
        dst.putUnitDouble(key, src.getUnitDoubleType(key), src.getUnitDoubleValue(key));
    } else if (type === DescValueType.STRINGTYPE) {
        dst.putString(key, src.getString(key));
    } else if (type === DescValueType.ENUMERATEDTYPE) {
        dst.putEnumerated(key, src.getEnumerationType(key), src.getEnumerationValue(key));
    } else if (type === DescValueType.CLASSTYPE) {
        dst.putClass(key, src.getClass(key));
    } else if (type === DescValueType.REFERENCETYPE) {
        dst.putReference(key, src.getReference(key));
    } else if (type === DescValueType.ALIASTYPE) {
        dst.putPath(key, src.getPath(key));
    } else if (type === DescValueType.RAWTYPE) {
        dst.putData(key, src.getData(key));
    } else if (type === DescValueType.LARGEINTEGERTYPE) {
        dst.putLargeInteger(key, src.getLargeInteger(key));
    } else {
        throw CR.fail("copy", "Colorroom met a setting it cannot copy safely (" + here + "), so it left this layer alone.");
    }
};

CR.recolorList = function (list, path, ctx) {
    var out = new ActionList(), i, type, pair;
    for (i = 0; i < list.count; i++) {
        type = list.getType(i);
        if (type === DescValueType.OBJECTTYPE) {
            pair = CR.recolorObject(list.getObjectValue(i), list.getObjectType(i), path + "/" + i, ctx);
            out.putObject(pair[0], pair[1]);
        } else if (type === DescValueType.LISTTYPE) {
            out.putList(CR.recolorList(list.getList(i), path + "/" + i, ctx));
        } else if (type === DescValueType.BOOLEANTYPE) {
            out.putBoolean(list.getBoolean(i));
        } else if (type === DescValueType.INTEGERTYPE) {
            out.putInteger(list.getInteger(i));
        } else if (type === DescValueType.DOUBLETYPE) {
            out.putDouble(list.getDouble(i));
        } else if (type === DescValueType.UNITDOUBLE) {
            out.putUnitDouble(list.getUnitDoubleType(i), list.getUnitDoubleValue(i));
        } else if (type === DescValueType.STRINGTYPE) {
            out.putString(list.getString(i));
        } else if (type === DescValueType.ENUMERATEDTYPE) {
            out.putEnumerated(list.getEnumerationType(i), list.getEnumerationValue(i));
        } else if (type === DescValueType.CLASSTYPE) {
            out.putClass(list.getClass(i));
        } else if (type === DescValueType.REFERENCETYPE) {
            out.putReference(list.getReference(i));
        } else if (type === DescValueType.ALIASTYPE) {
            out.putPath(list.getPath(i));
        } else if (type === DescValueType.RAWTYPE) {
            out.putData(list.getData(i));
        } else if (type === DescValueType.LARGEINTEGERTYPE) {
            out.putLargeInteger(list.getLargeInteger(i));
        } else {
            throw CR.fail("copy", "Colorroom met a setting it cannot copy safely (" + path + "/" + i + "), so it left this layer alone.");
        }
    }
    return out;
};

// Returns [classId, descriptor].
CR.recolorObject = function (obj, classId, path, ctx) {
    var info, wanted, before, copy;
    if (CR.isColor(classId)) {
        info = CR.hexOf(obj, classId);
        if (info && ctx.map.hasOwnProperty(info.hex) && (!ctx.accept || ctx.accept(path))) {
            wanted = ctx.map[info.hex];
            ctx.changes.push({ key: path, from: info.hex, to: wanted });
            return [CR.c("RGBC"), CR.rgbDesc(wanted)];
        }
        return [classId, obj];
    }
    before = ctx.changes.length;
    copy = CR.recolor(obj, path, ctx);
    // A gradient stop that followed the foreground or background color becomes a plain color.
    if (classId === CR.c("Clrt") && ctx.changes.length > before && copy.hasKey(CR.c("Type"))) {
        copy.putEnumerated(CR.c("Type"), CR.c("Clry"), CR.c("UsrS"));
    }
    return [classId, copy];
};

// ---- layers -----------------------------------------------------------------------------

CR.KIND = { 0: "other", 1: "image", 2: "adjustment", 3: "text", 4: "shape", 5: "smart-object", 6: "image", 7: "group", 8: "other", 9: "fill", 10: "fill", 11: "fill", 12: "image" };
CR.DETAIL = { 0: "layer", 1: "pixel layer", 2: "adjustment", 3: "text", 4: "shape", 5: "smart object", 6: "video", 7: "group", 8: "3D", 9: "gradient fill", 10: "pattern fill", 11: "solid color fill", 12: "background" };
CR.ADJUSTMENT = {
    gradientMapClass: "gradient map",
    photoFilter: "photo filter",
    blackAndWhite: "black & white",
    hueSaturation: "hue/saturation",
    colorBalance: "color balance",
    levels: "levels",
    curves: "curves",
    brightnessEvent: "brightness/contrast",
    channelMixer: "channel mixer",
    selectiveColor: "selective color",
    invert: "invert",
    posterize: "posterize",
    thresholdClassEvent: "threshold",
    vibrance: "vibrance",
    exposure: "exposure",
    colorLookup: "color lookup"
};
CR.LOCKS = [["protectAll", "all"], ["protectComposite", "pixels"], ["protectPosition", "position"], ["protectTransparency", "transparency"], ["protectArtboardAutonest", "artboard nesting"]];

CR.layerById = function (id) {
    var ref = new ActionReference();
    ref.putIdentifier(CR.c("Lyr "), id);
    return executeActionGet(ref);
};

CR.selectLayer = function (id) {
    var desc = new ActionDescriptor(), ref = new ActionReference();
    ref.putIdentifier(CR.c("Lyr "), id);
    desc.putReference(CR.c("null"), ref);
    desc.putBoolean(CR.c("MkVs"), false);
    executeAction(CR.c("slct"), desc, DialogModes.NO);
};

CR.targetRef = function (classId) {
    var ref = new ActionReference();
    ref.putEnumerated(classId, CR.c("Ordn"), CR.c("Trgt"));
    return ref;
};

CR.setLocking = function (locking) {
    var desc = new ActionDescriptor();
    desc.putReference(CR.c("null"), CR.targetRef(CR.c("Lyr ")));
    desc.putObject(CR.s("layerLocking"), CR.s("layerLocking"), locking);
    executeAction(CR.s("applyLocking"), desc, DialogModes.NO);
};

CR.installedFonts = function () {
    var ref, list, i, found = {};
    if (CR.fonts) {
        return CR.fonts;
    }
    try {
        ref = new ActionReference();
        ref.putProperty(CR.c("Prpr"), CR.s("fontList"));
        ref.putEnumerated(CR.c("capp"), CR.c("Ordn"), CR.c("Trgt"));
        list = executeActionGet(ref).getObjectValue(CR.s("fontList")).getList(CR.s("fontPostScriptName"));
        for (i = 0; i < list.count; i++) {
            found[list.getString(i)] = true;
        }
    } catch (e) {
        for (i = 0; i < app.fonts.length; i++) {
            found[app.fonts[i].postScriptName] = true;
        }
    }
    CR.fonts = found;
    return found;
};

CR.box = function (desc, key) {
    var b;
    if (!desc.hasKey(key)) {
        return null;
    }
    b = desc.getObjectValue(key);
    return [CR.number(b, CR.s("left")), CR.number(b, CR.s("top")), CR.number(b, CR.s("right")), CR.number(b, CR.s("bottom"))];
};

// Only the colors a person sees in a text layer: each run's own color, or the style it
// inherits when the run has none. Hidden defaults (paragraph styles, the unused text stroke
// color) are left out.
CR.textAccept = function (path) {
    return /^text\/textStyleRange\/\d+\/textStyle\/(baseParentStyle\/)?color$/.test(path);
};
CR.strokeAccept = function (path) {
    return /^stroke\/strokeStyleContent\//.test(path);
};

CR.textParts = function (textKey, parts, fonts, signature) {
    var list = textKey.getList(CR.s("textStyleRange")), i, item, style, from, to, found, own, j, font, size;
    for (i = 0; i < list.count; i++) {
        item = list.getObjectValue(i);
        from = item.getInteger(CR.s("from"));
        to = item.getInteger(CR.s("to"));
        style = item.getObjectValue(CR.s("textStyle"));
        font = style.hasKey(CR.s("fontPostScriptName")) ? style.getString(CR.s("fontPostScriptName")) : "";
        size = style.hasKey(CR.s("size")) ? Math.round(CR.number(style, CR.s("size")) * 1000) / 1000 : 0;
        signature.push([from, to, font, size]);
        if (font) {
            fonts[font] = true;
        }
        found = [];
        CR.scanObject(item, list.getObjectType(i), "text/textStyleRange/" + i, found, null);
        own = false;
        for (j = 0; j < found.length; j++) {
            if (/\/textStyle\/color$/.test(found[j].key)) {
                own = true;
            }
        }
        for (j = 0; j < found.length; j++) {
            if (CR.textAccept(found[j].key) && (own ? /\/textStyle\/color$/.test(found[j].key) : true)) {
                found[j].range = [from, to];
                parts.push(found[j]);
            }
        }
    }
};

// Layer effects: one entry per effect. When Photoshop lists an effect both alone and in its
// "Multi" list (several strokes, several overlays), the list is the true one.
CR.effectParts = function (fx, parts, signature) {
    var i, key, name, type, obj, list, j, found, k;
    var names = {};
    for (i = 0; i < fx.count; i++) {
        names[CR.name(fx.getKey(i))] = true;
    }
    for (i = 0; i < fx.count; i++) {
        key = fx.getKey(i);
        name = CR.name(key);
        type = fx.getType(key);
        if (type === DescValueType.OBJECTTYPE) {
            if (names[name + "Multi"]) {
                continue;
            }
            obj = fx.getObjectValue(key);
            CR.effectOne(obj, fx.getObjectType(key), "fx/" + name, parts, signature, name);
        } else if (type === DescValueType.LISTTYPE) {
            list = fx.getList(key);
            for (j = 0; j < list.count; j++) {
                if (list.getType(j) === DescValueType.OBJECTTYPE) {
                    CR.effectOne(list.getObjectValue(j), list.getObjectType(j), "fx/" + name + "/" + j, parts, signature, name);
                }
            }
        } else if (type === DescValueType.BOOLEANTYPE) {
            signature.push(name + "=" + fx.getBoolean(key));
        }
    }
};

CR.effectOne = function (obj, classId, path, parts, signature, name) {
    var found = [], k, on = obj.hasKey(CR.c("enab")) ? obj.getBoolean(CR.c("enab")) : true;
    signature.push(path + (on ? "+" : "-"));
    CR.scanObject(obj, classId, path, found, null);
    for (k = 0; k < found.length; k++) {
        found[k].effect = name;
        found[k].off = !on;
        parts.push(found[k]);
    }
};

// One layer as a plain object for the manifest.
CR.layerInfo = function (desc, groups) {
    var info = {}, kind, i, list, classId, locking, flags = [], fonts = {}, missing = [], font, signature = [], textSig = [];
    var parts = [], stroke, found, j, adjustments = [];
    info.id = desc.getInteger(CR.s("layerID"));
    info.name = desc.getString(CR.c("Nm  "));
    kind = desc.hasKey(CR.s("layerKind")) ? desc.getInteger(CR.s("layerKind")) : 0;
    if (desc.hasKey(CR.s("background")) && desc.getBoolean(CR.s("background"))) {
        kind = 12;
    }
    info.ps_kind = kind;
    info.kind = CR.KIND[kind] || "other";
    info.detail = CR.DETAIL[kind] || "layer";
    info.own_visible = desc.getBoolean(CR.c("Vsbl"));
    info.visible = info.own_visible;
    info.group_locked = "";
    for (i = 0; i < groups.length; i++) {
        if (!groups[i].visible) {
            info.visible = false;
        }
        if (groups[i].locked && !info.group_locked) {
            info.group_locked = groups[i].name;
        }
    }
    info.path = "";
    for (i = 0; i < groups.length; i++) {
        info.path += groups[i].name + " / ";
    }
    info.path += info.name;
    info.bounds = CR.box(desc, CR.s("bounds"));
    if (desc.hasKey(CR.s("layerLocking"))) {
        locking = desc.getObjectValue(CR.s("layerLocking"));
        for (i = 0; i < CR.LOCKS.length; i++) {
            if (locking.hasKey(CR.s(CR.LOCKS[i][0])) && locking.getBoolean(CR.s(CR.LOCKS[i][0]))) {
                flags.push(CR.LOCKS[i][1]);
            }
        }
    }
    info.lock = flags.join(", ");
    info.color_locked = info.lock.indexOf("all") >= 0 || info.lock.indexOf("pixels") >= 0;
    info.locked = flags.length > 0 || kind === 12;
    info.notes = [];
    if (desc.hasKey(CR.s("adjustment"))) {
        list = desc.getList(CR.s("adjustment"));
        for (i = 0; i < list.count; i++) {
            classId = list.getObjectType(i);
            adjustments.push(CR.name(classId));
            CR.scanObject(list.getObjectValue(i), classId, "adj/" + i + "/" + CR.name(classId), parts, null);
        }
        if (kind === 2 && adjustments.length) {
            info.detail = CR.ADJUSTMENT[adjustments[0]] || adjustments[0];
        }
    }
    if (desc.hasKey(CR.s("AGMStrokeStyleInfo"))) {
        stroke = desc.getObjectValue(CR.s("AGMStrokeStyleInfo"));
        found = [];
        CR.scan(stroke, "stroke", found, null);
        info.stroke_on = stroke.hasKey(CR.s("strokeEnabled")) ? stroke.getBoolean(CR.s("strokeEnabled")) : true;
        info.fill_on = stroke.hasKey(CR.s("fillEnabled")) ? stroke.getBoolean(CR.s("fillEnabled")) : true;
        for (j = 0; j < found.length; j++) {
            if (CR.strokeAccept(found[j].key)) {
                found[j].off = !info.stroke_on;
                parts.push(found[j]);
            }
        }
        if (!info.fill_on) {
            for (j = 0; j < parts.length; j++) {
                if (/^adj\//.test(parts[j].key)) {
                    parts[j].off = true;
                }
            }
        }
    }
    if (desc.hasKey(CR.s("layerEffects"))) {
        CR.effectParts(desc.getObjectValue(CR.s("layerEffects")), parts, signature);
    }
    if (kind === 3 && desc.hasKey(CR.s("textKey"))) {
        CR.textParts(desc.getObjectValue(CR.s("textKey")), parts, fonts, textSig);
        info.text = desc.getObjectValue(CR.s("textKey")).getString(CR.s("textKey"));
        for (font in fonts) {
            if (fonts.hasOwnProperty(font) && !CR.installedFonts()[font]) {
                missing.push(font);
            }
        }
        info.missing_fonts = missing;
        if (missing.length) {
            info.notes.push("Uses a font that is not installed on this computer: " + missing.join(", ") + ".");
        }
    }
    for (j = 0; j < parts.length; j++) {
        if (parts[j].hex === null) {
            info.notes.push("Uses a color Colorroom cannot read (" + parts[j].model + " color at " + parts[j].key + ").");
        }
    }
    info.adjustments = adjustments;
    info.parts = parts;
    info.signature = CR.json([info.name, kind, info.own_visible, info.bounds ? [Math.round(info.bounds[0]), Math.round(info.bounds[1]), Math.round(info.bounds[2]), Math.round(info.bounds[3])] : null, info.lock, adjustments, signature, info.text || null, textSig, info.stroke_on === undefined ? null : info.stroke_on]);
    return info;
};

// Every layer, top of the Layers panel first, with its group path.
CR.readLayers = function () {
    var ref = new ActionReference(), docDesc, count, i, desc, section, info, groups = [], layers = [], seen = {};
    ref.putEnumerated(CR.c("Dcmn"), CR.c("Ordn"), CR.c("Trgt"));
    docDesc = executeActionGet(ref);
    count = docDesc.getInteger(CR.s("numberOfLayers"));
    for (i = count; i >= 0; i--) {
        ref = new ActionReference();
        ref.putIndex(CR.c("Lyr "), i);
        try {
            desc = executeActionGet(ref);
        } catch (e) {
            continue;
        }
        if (!desc.hasKey(CR.s("layerID")) || seen[desc.getInteger(CR.s("layerID"))]) {
            continue;
        }
        seen[desc.getInteger(CR.s("layerID"))] = true;
        section = desc.hasKey(CR.s("layerSection")) ? CR.name(desc.getEnumerationValue(CR.s("layerSection"))) : "layerSectionContent";
        if (section === "layerSectionEnd") {
            groups.pop();
            continue;
        }
        info = CR.layerInfo(desc, groups);
        info.order = layers.length;
        layers.push(info);
        if (section === "layerSectionStart") {
            groups.push({ name: info.name, visible: info.visible, locked: info.lock.indexOf("all") >= 0 });
        }
    }
    return layers;
};

CR.MODES = { "DocumentMode.RGB": "RGB", "DocumentMode.CMYK": "CMYK", "DocumentMode.GRAYSCALE": "Grayscale", "DocumentMode.LAB": "Lab", "DocumentMode.INDEXEDCOLOR": "Indexed", "DocumentMode.BITMAP": "Bitmap", "DocumentMode.MULTICHANNEL": "Multichannel", "DocumentMode.DUOTONE": "Duotone" };
CR.BITS = { "BitsPerChannelType.ONE": 1, "BitsPerChannelType.EIGHT": 8, "BitsPerChannelType.SIXTEEN": 16, "BitsPerChannelType.THIRTYTWO": 32 };

CR.docPath = function (doc) {
    try {
        return doc.fullName.fsName;
    } catch (e) {
        return "";
    }
};

// The whole document as a Colorroom manifest (manifest.py's format).
CR.manifest = function (doc) {
    var out = {}, profile = "";
    app.activeDocument = doc;
    app.preferences.rulerUnits = Units.PIXELS;
    try {
        profile = doc.colorProfileName;
    } catch (e) {}
    out.format = "colorroom-manifest";
    out.manifest_version = 1;
    out.program = "Photoshop";
    out.program_version = String(app.version);
    out.document = doc.name;
    out.source = CR.docPath(doc);
    out.saved = doc.saved;
    out.color_mode = CR.MODES[String(doc.mode)] || String(doc.mode);
    out.bit_depth = CR.BITS[String(doc.bitsPerChannel)] || 0;
    out.width = Math.round(doc.width.as("px"));
    out.height = Math.round(doc.height.as("px"));
    out.resolution = doc.resolution;
    out.color_profile = profile;
    out.layer_comps = doc.layerComps.length;
    out.layers = CR.readLayers();
    out.notes = [];
    return out;
};

CR.samePath = function (a, b) {
    return String(a).toLowerCase().replace(/\//g, "\\") === String(b).toLowerCase().replace(/\//g, "\\");
};

CR.findOpen = function (path) {
    var wanted = new File(path).fsName, i;
    for (i = 0; i < app.documents.length; i++) {
        if (CR.samePath(CR.docPath(app.documents[i]), wanted)) {
            return app.documents[i];
        }
    }
    return null;
};

CR.open = function (path) {
    var file = new File(path);
    if (!file.exists) {
        throw CR.fail("missing", "Photoshop cannot find the file " + file.fsName + ".");
    }
    try {
        return app.open(file);
    } catch (e) {
        throw CR.fail("open", "Photoshop could not open " + file.name + ": " + e.message);
    }
};

// A flattened picture of what the document shows, as PNG. Works on a merged duplicate,
// never on the document itself.
CR.savePreview = function (doc, path) {
    var copy, options;
    app.activeDocument = doc;
    copy = doc.duplicate("Colorroom preview", true);
    try {
        if (copy.mode !== DocumentMode.RGB) {
            if (copy.mode === DocumentMode.BITMAP) {
                copy.changeMode(ChangeMode.GRAYSCALE);
            }
            copy.changeMode(ChangeMode.RGB);
        }
        if (copy.bitsPerChannel !== BitsPerChannelType.EIGHT) {
            copy.bitsPerChannel = BitsPerChannelType.EIGHT;
        }
        options = new PNGSaveOptions();
        options.interlaced = false;
        copy.saveAs(new File(path), options, true, Extension.LOWERCASE);
    } finally {
        copy.close(SaveOptions.DONOTSAVECHANGES);
    }
};
