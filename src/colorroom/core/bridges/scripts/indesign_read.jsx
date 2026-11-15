// Made by Opus 5.5. Colorroom, InDesign bridge: read a document's colors. Changes nothing.
//
// Arguments (CR_ARGS): {path: "C:/.../file.indd" or "" for the active document}
// Returns JSON: the document, its pages and layers, every swatch and unnamed color with its
// values and InDesign's own RGB for it, how many page items and text ranges use each one,
// placed images, fonts and links that need attention, and item counts for later comparison.
//@include "indesign_lib.jsx"

function readColor(color, out) {
    var space = CR.enumName(CR.get(color, "space", null));
    var values = CR.values(CR.get(color, "colorValue", []));
    var rgb = null;
    out.kind = "color";
    out.model = CR.enumName(CR.get(color, "model", null));
    out.space = space;
    out.values = values;
    if (space === "RGB" || space === "CMYK" || space === "LAB") {
        try { rgb = CR.toRGB(space, values); } catch (e) { rgb = null; }
    }
    out.rgb = rgb;
    out.hex = rgb ? CR.hex(rgb) : null;
    out.hexExact = space === "RGB";
}

function readTint(tint, out) {
    var base = CR.get(tint, "baseColor", null);
    var amount = Number(CR.get(tint, "tintValue", 100));
    var space, values, scaled = [], rgb = null, i;
    out.kind = "tint";
    out.base = CR.swatchKey(base);
    out.tint = amount;
    if (base && CR.get(base, "colorValue", null)) {
        space = CR.enumName(base.space);
        values = CR.values(base.colorValue);
        out.model = CR.enumName(base.model);
        out.space = space;
        try {
            if (space === "CMYK") {
                // A tint of a process or spot color prints less of each ink.
                for (i = 0; i < values.length; i++) { scaled.push(values[i] * amount / 100); }
                rgb = CR.toRGB("CMYK", scaled);
            } else {
                rgb = CR.toRGB(space, values);
                for (i = 0; rgb && i < 3; i++) { rgb[i] = 255 - (255 - rgb[i]) * amount / 100; }
            }
        } catch (e) { rgb = null; }
    }
    out.rgb = rgb;
    out.hex = rgb ? CR.hex(rgb) : null;
    out.hexExact = false;
}

function readGradient(gradient, out) {
    var stops = gradient.gradientStops, i, stop;
    out.kind = "gradient";
    out.gradientType = CR.enumName(CR.get(gradient, "type", null));
    out.stops = [];
    for (i = 0; i < stops.length; i++) {
        stop = stops[i];
        out.stops.push({
            color: CR.swatchKey(CR.get(stop, "stopColor", null)),
            location: Number(CR.get(stop, "location", 0)),
            midpoint: Number(CR.get(stop, "midpoint", 50))
        });
    }
}

function readSwatch(swatch, listed) {
    // Items of doc.swatches come back as plain Swatch objects; getElements gives the real
    // Color, Tint, Gradient or MixedInk.
    try { swatch = swatch.getElements()[0]; } catch (e) {}
    var kind = String(swatch.constructor.name);
    var out = {
        id: String(swatch.id),
        name: String(swatch.name),
        listed: listed, // in the Swatches panel
        named: String(swatch.name) !== "",
        type: kind,
        editable: CR.get(swatch, "colorEditable", true) !== false,
        removable: CR.get(swatch, "colorRemovable", true) !== false,
        uses: 0, textUses: 0, lockedUses: 0, stopUses: 0, tintUses: 0
    };
    if (kind === "Color") {
        readColor(swatch, out);
    } else if (kind === "Tint") {
        readTint(swatch, out);
    } else if (kind === "Gradient") {
        readGradient(swatch, out);
    } else if (kind === "MixedInk") {
        out.kind = "mixed-ink";
    } else {
        out.kind = "none";
    }
    return out;
}

function count(table, key, field) {
    if (key !== null && table[key]) {
        table[key][field] += 1;
    }
}

function main(args) {
    var found = CR.document(args.path || "");
    var doc = found.doc;
    var result = {}, swatches = [], byId = {}, i, j, item, list, layers = [], spreads = [];
    var kinds = {}, items = 0, locked = 0, key, stories, ranges = 0, characters = 0, fills, strokes;

    CR.openScratch(doc);

    list = doc.swatches;
    for (i = 0; i < list.length; i++) {
        item = readSwatch(list[i], true);
        swatches.push(item);
        byId[item.id] = item;
    }
    // Unnamed colors and gradients are not in the Swatches panel, but frames and text use them.
    list = doc.colors;
    for (i = 0; i < list.length; i++) {
        if (!byId[String(list[i].id)]) {
            item = readSwatch(list[i], false);
            swatches.push(item);
            byId[item.id] = item;
        }
    }
    list = doc.gradients;
    for (i = 0; i < list.length; i++) {
        if (!byId[String(list[i].id)]) {
            item = readSwatch(list[i], false);
            swatches.push(item);
            byId[item.id] = item;
        }
    }
    for (i = 0; i < swatches.length; i++) {
        if (swatches[i].kind === "tint") { count(byId, swatches[i].base, "tintUses"); }
        if (swatches[i].kind === "gradient") {
            for (j = 0; j < swatches[i].stops.length; j++) { count(byId, swatches[i].stops[j].color, "stopUses"); }
        }
    }

    // Page items on pages and on master spreads (parents).
    function walk(spreadList, master) {
        var s, all, k, it, name, fill, stroke, isLocked, info;
        for (s = 0; s < spreadList.length; s++) {
            all = spreadList[s].allPageItems;
            info = {master: master, name: String(CR.get(spreadList[s], "name", "")), pages: spreadList[s].pages.length, items: all.length};
            spreads.push(info);
            for (k = 0; k < all.length; k++) {
                it = all[k];
                name = String(it.constructor.name);
                kinds[name] = (kinds[name] || 0) + 1;
                items += 1;
                isLocked = CR.get(it, "locked", false) === true;
                if (isLocked) { locked += 1; }
                fill = CR.swatchKey(CR.get(it, "fillColor", null));
                stroke = CR.swatchKey(CR.get(it, "strokeColor", null));
                count(byId, fill, "uses");
                count(byId, stroke, "uses");
                if (isLocked) {
                    count(byId, fill, "lockedUses");
                    count(byId, stroke, "lockedUses");
                }
            }
        }
    }
    walk(doc.spreads, false);
    walk(doc.masterSpreads, true);

    // Text: every run of text with its own fill and stroke.
    stories = doc.stories;
    for (i = 0; i < stories.length; i++) {
        list = stories[i].textStyleRanges;
        characters += stories[i].characters.length;
        if (list.length === 0) { continue; }
        ranges += list.length;
        fills = list.everyItem().fillColor;
        strokes = list.everyItem().strokeColor;
        for (j = 0; j < fills.length; j++) {
            key = CR.swatchKey(fills[j]);
            count(byId, key, "textUses");
            key = CR.swatchKey(strokes[j]);
            count(byId, key, "textUses");
        }
    }

    for (i = 0; i < doc.layers.length; i++) {
        item = doc.layers[i];
        layers.push({name: String(item.name), visible: item.visible === true, locked: item.locked === true});
    }

    var graphics = doc.allGraphics, images = {}, fonts = [], links = [], status;
    for (i = 0; i < graphics.length; i++) {
        key = String(graphics[i].constructor.name);
        images[key] = (images[key] || 0) + 1;
    }
    list = doc.fonts;
    for (i = 0; i < list.length; i++) {
        status = CR.enumName(CR.get(list[i], "status", null));
        if (status !== "INSTALLED") {
            fonts.push({name: String(CR.get(list[i], "name", "")), status: status});
        }
    }
    list = doc.links;
    for (i = 0; i < list.length; i++) {
        status = CR.enumName(CR.get(list[i], "status", null));
        if (status !== "NORMAL" && status !== "LINK_EMBEDDED") {
            links.push({name: String(CR.get(list[i], "name", "")), status: status});
        }
    }

    var prefs = doc.documentPreferences;
    result.program = "InDesign";
    result.programVersion = String(app.version);
    result.document = String(doc.name);
    result.path = doc.saved ? File(doc.fullName).fsName : "";
    result.openedByColorroom = found.ours;
    result.modified = CR.get(doc, "modified", false) === true;
    result.intent = CR.enumName(CR.get(prefs, "intent", null));
    result.blendingSpace = CR.enumName(CR.get(doc.transparencyPreferences, "blendingSpace", null));
    result.rgbProfile = String(CR.get(doc, "rgbProfile", ""));
    result.cmykProfile = String(CR.get(doc, "cmykProfile", ""));
    result.pageWidth = Number(prefs.pageWidth);
    result.pageHeight = Number(prefs.pageHeight);
    result.pages = doc.pages.length;
    result.spreads = doc.spreads.length;
    result.masterSpreads = doc.masterSpreads.length;
    result.spreadList = spreads;
    result.layers = layers;
    result.swatches = swatches;
    result.items = items;
    result.itemKinds = kinds;
    result.lockedItems = locked;
    result.stories = stories.length;
    result.textRanges = ranges;
    result.characters = characters;
    result.images = graphics.length;
    result.imageKinds = images;
    result.fontsNeedingAttention = fonts;
    result.linksNeedingAttention = links;
    return result;
}

CR.run(main);
