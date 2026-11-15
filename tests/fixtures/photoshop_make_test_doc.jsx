//@include "photoshop_lib.jsx"
// Made by Opus 5.5 (2026-11-14). Builds the Colorroom Photoshop test document INSIDE
// Photoshop and saves it, so the round trip is tested on a file Photoshop itself wrote.
// Used by tests/test_bridge_photoshop.py (live tests only). Never touches any other file.
// CR_ARGS: { path: "C:/.../test-kit/ps-bridge-test.psd" }
//
// Layers, bottom to top:
//   Background (white, Photoshop's own locked background)
//   Photo pixels   pixel layer, two flat rectangles #3366CC and #CC6633
//   Smart photo    smart object holding a #3366CC rectangle
//   Gray ramp      pixel layer, five gray bands (0, 64, 128, 192, 255)
//   Gradient Map   adjustment clipped to Gray ramp: #1D3557 0%, #F4A261 50%, #F1FAEE 100%
//   Solid fill     solid color fill #E63946 (masked to a rectangle)
//   Gradient fill  gradient fill #A8DADC to #6D597A (masked)
//   Rectangle      shape, fill #457B9D, 6 px stroke #1D3557
//   Group A        group
//     Badge        pixel layer with Color Overlay #2A9D8F and Stroke effect #264653
//   Locked fill    solid color fill #E63946, all locked
//   Hidden fill    solid color fill #E63946, hidden
//   Headline       text "Colorroom Test": "Colorroom " #111827, "Test" #E63946
//   Caption        text "one color", all #1D3557
// Plus one layer comp, "Comp 1".

CR.T = {};

CR.T.rect = function (doc, left, top, right, bottom) {
    doc.selection.select([[left, top], [right, top], [right, bottom], [left, bottom]]);
};

CR.T.fillRect = function (doc, left, top, right, bottom, hex) {
    CR.T.rect(doc, left, top, right, bottom);
    doc.selection.fill(CR.solid(hex));
    doc.selection.deselect();
};

CR.T.gradient = function (stops) {
    var gradient = new ActionDescriptor(), colors = new ActionList(), alphas = new ActionList(), i, stop, alpha;
    gradient.putString(CR.c("Nm  "), "Colorroom test");
    gradient.putEnumerated(CR.c("GrdF"), CR.c("GrdF"), CR.c("CstS"));
    gradient.putDouble(CR.c("Intr"), 4096);
    for (i = 0; i < stops.length; i++) {
        stop = new ActionDescriptor();
        stop.putObject(CR.c("Clr "), CR.c("RGBC"), CR.rgbDesc(stops[i][0]));
        stop.putEnumerated(CR.c("Type"), CR.c("Clry"), CR.c("UsrS"));
        stop.putInteger(CR.c("Lctn"), Math.round(stops[i][1] * 4096));
        stop.putInteger(CR.c("Mdpn"), 50);
        colors.putObject(CR.c("Clrt"), stop);
    }
    gradient.putList(CR.c("Clrs"), colors);
    for (i = 0; i < 2; i++) {
        alpha = new ActionDescriptor();
        alpha.putUnitDouble(CR.c("Opct"), CR.c("#Prc"), 100);
        alpha.putInteger(CR.c("Lctn"), i * 4096);
        alpha.putInteger(CR.c("Mdpn"), 50);
        alphas.putObject(CR.c("TrnS"), alpha);
    }
    gradient.putList(CR.c("Trns"), alphas);
    return gradient;
};

CR.T.make = function (typeId, content, extra) {
    var desc = new ActionDescriptor(), ref = new ActionReference(), layer = new ActionDescriptor(), key;
    ref.putClass(CR.s("contentLayer"));
    desc.putReference(CR.c("null"), ref);
    layer.putObject(CR.c("Type"), typeId, content);
    if (extra) {
        extra(layer);
    }
    desc.putObject(CR.c("Usng"), CR.s("contentLayer"), layer);
    executeAction(CR.c("Mk  "), desc, DialogModes.NO);
};

CR.T.solidFill = function (doc, name, hex, box) {
    var content = new ActionDescriptor();
    CR.T.rect(doc, box[0], box[1], box[2], box[3]);
    content.putObject(CR.c("Clr "), CR.c("RGBC"), CR.rgbDesc(hex));
    CR.T.make(CR.s("solidColorLayer"), content);
    doc.selection.deselect();
    doc.activeLayer.name = name;
    return doc.activeLayer;
};

CR.T.gradientFill = function (doc, name, stops, box) {
    var content = new ActionDescriptor();
    CR.T.rect(doc, box[0], box[1], box[2], box[3]);
    content.putObject(CR.c("Grad"), CR.c("Grdn"), CR.T.gradient(stops));
    content.putEnumerated(CR.c("Type"), CR.c("GrdT"), CR.c("Lnr "));
    content.putUnitDouble(CR.c("Angl"), CR.c("#Ang"), 0);
    CR.T.make(CR.s("gradientLayer"), content);
    doc.selection.deselect();
    doc.activeLayer.name = name;
    return doc.activeLayer;
};

CR.T.shape = function (doc, name, fillHex, strokeHex, box) {
    var content = new ActionDescriptor();
    content.putObject(CR.c("Clr "), CR.c("RGBC"), CR.rgbDesc(fillHex));
    CR.T.make(CR.s("solidColorLayer"), content, function (layer) {
        var rect = new ActionDescriptor(), stroke = new ActionDescriptor(), strokeContent = new ActionDescriptor();
        rect.putInteger(CR.s("unitValueQuadVersion"), 1);
        rect.putUnitDouble(CR.s("top"), CR.s("pixelsUnit"), box[1]);
        rect.putUnitDouble(CR.s("left"), CR.s("pixelsUnit"), box[0]);
        rect.putUnitDouble(CR.s("bottom"), CR.s("pixelsUnit"), box[3]);
        rect.putUnitDouble(CR.s("right"), CR.s("pixelsUnit"), box[2]);
        layer.putObject(CR.s("shape"), CR.s("rectangle"), rect);
        stroke.putInteger(CR.s("strokeStyleVersion"), 2);
        stroke.putBoolean(CR.s("strokeEnabled"), true);
        stroke.putBoolean(CR.s("fillEnabled"), true);
        stroke.putUnitDouble(CR.s("strokeStyleLineWidth"), CR.s("pixelsUnit"), 6);
        stroke.putUnitDouble(CR.s("strokeStyleLineDashOffset"), CR.s("pointsUnit"), 0);
        stroke.putDouble(CR.s("strokeStyleMiterLimit"), 100);
        stroke.putEnumerated(CR.s("strokeStyleLineCapType"), CR.s("strokeStyleLineCapType"), CR.s("strokeStyleButtCap"));
        stroke.putEnumerated(CR.s("strokeStyleLineJoinType"), CR.s("strokeStyleLineJoinType"), CR.s("strokeStyleMiterJoin"));
        stroke.putEnumerated(CR.s("strokeStyleLineAlignment"), CR.s("strokeStyleLineAlignment"), CR.s("strokeStyleAlignInside"));
        stroke.putBoolean(CR.s("strokeStyleScaleLock"), false);
        stroke.putBoolean(CR.s("strokeStyleStrokeAdjust"), false);
        stroke.putList(CR.s("strokeStyleLineDashSet"), new ActionList());
        stroke.putEnumerated(CR.s("strokeStyleBlendMode"), CR.s("blendMode"), CR.s("normal"));
        stroke.putUnitDouble(CR.s("strokeStyleOpacity"), CR.s("percentUnit"), 100);
        strokeContent.putObject(CR.c("Clr "), CR.c("RGBC"), CR.rgbDesc(strokeHex));
        stroke.putObject(CR.s("strokeStyleContent"), CR.s("solidColorLayer"), strokeContent);
        stroke.putDouble(CR.s("strokeStyleResolution"), 72);
        layer.putObject(CR.s("strokeStyle"), CR.s("strokeStyle"), stroke);
    });
    doc.activeLayer.name = name;
    return doc.activeLayer;
};

CR.T.gradientMap = function (doc, name, stops) {
    var desc = new ActionDescriptor(), ref = new ActionReference(), layer = new ActionDescriptor(), map = new ActionDescriptor();
    ref.putClass(CR.c("AdjL"));
    desc.putReference(CR.c("null"), ref);
    layer.putString(CR.c("Nm  "), name);
    layer.putBoolean(CR.c("Grup"), true);
    map.putObject(CR.c("Grad"), CR.c("Grdn"), CR.T.gradient(stops));
    layer.putObject(CR.c("Type"), CR.c("GdMp"), map);
    desc.putObject(CR.c("Usng"), CR.c("AdjL"), layer);
    executeAction(CR.c("Mk  "), desc, DialogModes.NO);
    return doc.activeLayer;
};

CR.T.effects = function (overlayHex, strokeHex) {
    var desc = new ActionDescriptor(), ref = new ActionReference(), fx = new ActionDescriptor(), overlay = new ActionDescriptor(), stroke = new ActionDescriptor();
    ref.putProperty(CR.c("Prpr"), CR.c("Lefx"));
    ref.putEnumerated(CR.c("Lyr "), CR.c("Ordn"), CR.c("Trgt"));
    desc.putReference(CR.c("null"), ref);
    fx.putUnitDouble(CR.c("Scl "), CR.c("#Prc"), 100);
    overlay.putBoolean(CR.c("enab"), true);
    overlay.putEnumerated(CR.c("Md  "), CR.c("BlnM"), CR.c("Nrml"));
    overlay.putUnitDouble(CR.c("Opct"), CR.c("#Prc"), 100);
    overlay.putObject(CR.c("Clr "), CR.c("RGBC"), CR.rgbDesc(overlayHex));
    fx.putObject(CR.c("SoFi"), CR.c("SoFi"), overlay);
    stroke.putBoolean(CR.c("enab"), true);
    stroke.putEnumerated(CR.c("Styl"), CR.c("FStl"), CR.c("OutF"));
    stroke.putEnumerated(CR.c("PntT"), CR.c("FrFl"), CR.c("SClr"));
    stroke.putEnumerated(CR.c("Md  "), CR.c("BlnM"), CR.c("Nrml"));
    stroke.putUnitDouble(CR.c("Opct"), CR.c("#Prc"), 100);
    stroke.putUnitDouble(CR.c("Sz  "), CR.c("#Pxl"), 4);
    stroke.putObject(CR.c("Clr "), CR.c("RGBC"), CR.rgbDesc(strokeHex));
    fx.putObject(CR.c("FrFX"), CR.c("FrFX"), stroke);
    desc.putObject(CR.c("T   "), CR.c("Lefx"), fx);
    executeAction(CR.c("setd"), desc, DialogModes.NO);
};

CR.T.text = function (doc, name, words, split, firstHex, secondHex, size, top) {
    var layer = doc.artLayers.add(), item, desc, textKey, style, list, length, copy, set;
    layer.kind = LayerKind.TEXT;
    layer.name = name;
    item = layer.textItem;
    item.contents = words;
    if (CR.installedFonts()["ArialMT"]) {
        item.font = "ArialMT";
    }
    item.size = new UnitValue(size, "pt");
    item.position = [new UnitValue(40, "px"), new UnitValue(top, "px")];
    item.color = CR.solid(firstHex);
    if (!secondHex) {
        return layer;
    }
    desc = CR.layerById(layer.id);
    textKey = desc.getObjectValue(CR.s("textKey"));
    length = textKey.getString(CR.s("textKey")).length;
    style = textKey.getList(CR.s("textStyleRange")).getObjectValue(0).getObjectValue(CR.s("textStyle"));

    function run(from, to, hex) {
        var range = new ActionDescriptor(), own = CR.recolor(style, "t", { map: {}, changes: [] });
        own.putObject(CR.s("color"), CR.c("RGBC"), CR.rgbDesc(hex));
        range.putInteger(CR.s("from"), from);
        range.putInteger(CR.s("to"), to);
        range.putObject(CR.s("textStyle"), CR.s("textStyle"), own);
        return range;
    }
    list = new ActionList();
    list.putObject(CR.s("textStyleRange"), run(0, split, firstHex));
    list.putObject(CR.s("textStyleRange"), run(split, length, secondHex));
    copy = CR.recolor(textKey, "t", { map: {}, changes: [] });
    copy.putList(CR.s("textStyleRange"), list);
    set = new ActionDescriptor();
    set.putReference(CR.c("null"), CR.targetRef(CR.c("TxLr")));
    set.putObject(CR.c("T   "), CR.c("TxLr"), copy);
    executeAction(CR.c("setd"), set, DialogModes.NO);
    return layer;
};

CR.run(function (args) {
    var doc, layer, group, options, typeUnits = app.preferences.typeUnits, steps = [];
    app.preferences.rulerUnits = Units.PIXELS;
    app.preferences.typeUnits = TypeUnits.POINTS;
    try {
        doc = app.documents.add(800, 600, 144, "Colorroom bridge test", NewDocumentMode.RGB, DocumentFill.WHITE, 1, BitsPerChannelType.EIGHT);
        layer = doc.artLayers.add();
        layer.name = "Photo pixels";
        CR.T.fillRect(doc, 20, 380, 200, 580, "#3366CC");
        CR.T.fillRect(doc, 200, 380, 380, 580, "#CC6633");
        steps.push("pixels");
        layer = doc.artLayers.add();
        layer.name = "Smart photo";
        CR.T.fillRect(doc, 20, 20, 60, 60, "#3366CC");
        executeAction(CR.s("newPlacedLayer"), new ActionDescriptor(), DialogModes.NO);
        doc.activeLayer.name = "Smart photo";
        steps.push("smart object");
        layer = doc.artLayers.add();
        layer.name = "Gray ramp";
        CR.T.fillRect(doc, 420, 380, 488, 580, "#000000");
        CR.T.fillRect(doc, 488, 380, 556, 580, "#404040");
        CR.T.fillRect(doc, 556, 380, 624, 580, "#808080");
        CR.T.fillRect(doc, 624, 380, 692, 580, "#C0C0C0");
        CR.T.fillRect(doc, 692, 380, 760, 580, "#FFFFFF");
        steps.push("gray");
        CR.T.gradientMap(doc, "Gradient Map", [["#1D3557", 0], ["#F4A261", 0.5], ["#F1FAEE", 1]]);
        steps.push("gradient map");
        CR.T.solidFill(doc, "Solid fill", "#E63946", [20, 150, 180, 340]);
        steps.push("solid fill");
        CR.T.gradientFill(doc, "Gradient fill", [["#A8DADC", 0], ["#6D597A", 1]], [200, 150, 380, 340]);
        steps.push("gradient fill");
        CR.T.shape(doc, "Rectangle", "#457B9D", "#1D3557", [400, 150, 560, 340]);
        steps.push("shape");
        group = doc.layerSets.add();
        group.name = "Group A";
        layer = group.artLayers.add();
        layer.name = "Badge";
        doc.activeLayer = layer;
        CR.T.fillRect(doc, 600, 170, 760, 320, "#CCCCCC");
        CR.T.effects("#2A9D8F", "#264653");
        steps.push("effects");
        doc.activeLayer = group;
        layer = CR.T.solidFill(doc, "Locked fill", "#E63946", [600, 20, 680, 80]);
        layer.allLocked = true;
        steps.push("locked");
        layer = CR.T.solidFill(doc, "Hidden fill", "#E63946", [690, 20, 770, 80]);
        layer.visible = false;
        steps.push("hidden");
        CR.T.text(doc, "Headline", "Colorroom Test", 10, "#111827", "#E63946", 40, 90);
        CR.T.text(doc, "Caption", "one color", 0, "#1D3557", null, 14, 125);
        steps.push("text");
        doc.layerComps.add("Comp 1", "", true, true, true);
        steps.push("layer comp");
        options = new PhotoshopSaveOptions();
        options.layers = true;
        options.embedColorProfile = true;
        options.alphaChannels = true;
        options.annotations = true;
        options.spotColors = true;
        doc.saveAs(new File(args.path), options, false, Extension.LOWERCASE);
        steps.push("saved");
    } catch (e) {
        e.message = "step after [" + steps.join(", ") + "]: " + e.message;
        throw e;
    } finally {
        app.preferences.typeUnits = typeUnits;
        if (doc) {
            doc.close(SaveOptions.DONOTSAVECHANGES);
        }
    }
    return { path: args.path, steps: steps };
});
