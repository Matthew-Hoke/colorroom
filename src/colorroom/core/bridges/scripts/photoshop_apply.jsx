//@include "photoshop_lib.jsx"
// Made by Opus 5.5 (2026-11-14). Colorroom, Photoshop bridge: change exact colors in a copy.
// One job: open the copy Colorroom made beside the original, change every color named in the
// mapping on the real layers (fills, shapes, strokes, text runs, gradient fills, gradient
// maps, other adjustment colors, layer effects) in one history step, save it in place and
// close it. Colorroom then opens the saved file again with photoshop_read.jsx and checks
// every color.
// Never opens the original: Colorroom refuses before it gets here if the paths are the same.
// CR_ARGS: { path: "C:/.../name - Colorroom A.psd",
//            map: { "#OLDHEX": "#NEWHEX", ... },
//            hidden: true   (change hidden layers too),
//            locked: false  (unlock, change and lock again layers locked in Photoshop) }

CR.applyLayer = function (layer, job) {
    var map = job.map, desc, list, i, classId, ctx, change, target, set, words = [];
    var hits = { adj: false, stroke: false, fx: false, text: false }, j, key, relock = null, done = [];
    for (j = 0; j < layer.parts.length; j++) {
        if (layer.parts[j].hex !== null && map.hasOwnProperty(layer.parts[j].hex)) {
            key = layer.parts[j].key.split("/")[0];
            hits[key] = true;
        }
    }
    if (!hits.adj && !hits.stroke && !hits.fx && !hits.text) {
        return;
    }
    if (!layer.visible && !job.hidden) {
        job.skipped.push({ id: layer.id, reason: "hidden" });
        return;
    }
    if (layer.group_locked) {
        job.skipped.push({ id: layer.id, reason: "group-locked", detail: layer.group_locked });
        return;
    }
    if (layer.color_locked && !job.locked) {
        job.skipped.push({ id: layer.id, reason: "locked", detail: layer.lock });
        return;
    }
    if (hits.text && layer.missing_fonts && layer.missing_fonts.length) {
        job.skipped.push({ id: layer.id, reason: "missing-font", detail: layer.missing_fonts.join(", "), part: "text" });
        hits.text = false;
        if (!hits.adj && !hits.stroke && !hits.fx) {
            return;
        }
    }
    CR.selectLayer(layer.id);
    desc = CR.layerById(layer.id);
    if (layer.color_locked) {
        relock = desc.getObjectValue(CR.s("layerLocking"));
        set = new ActionDescriptor();
        set.putBoolean(CR.s("protectNone"), true);
        CR.setLocking(set);
    }
    try {
        if (hits.adj && desc.hasKey(CR.s("adjustment"))) {
            words.push("fill");
            try {
                list = desc.getList(CR.s("adjustment"));
                target = layer.ps_kind === 2 ? CR.c("AdjL") : CR.s("contentLayer");
                for (i = 0; i < list.count; i++) {
                    classId = list.getObjectType(i);
                    ctx = { map: map, changes: [], accept: null };
                    change = CR.recolor(list.getObjectValue(i), "adj/" + i + "/" + CR.name(classId), ctx);
                    if (ctx.changes.length) {
                        set = new ActionDescriptor();
                        set.putReference(CR.c("null"), CR.targetRef(target));
                        set.putObject(CR.c("T   "), classId, change);
                        executeAction(CR.c("setd"), set, DialogModes.NO);
                        done = done.concat(ctx.changes);
                    }
                }
            } catch (e1) {
                job.errors.push({ id: layer.id, part: layer.ps_kind === 2 ? "adjustment" : "fill", message: String(e1.message || e1) });
            }
        }
        if (hits.stroke && desc.hasKey(CR.s("AGMStrokeStyleInfo"))) {
            try {
                ctx = { map: map, changes: [], accept: CR.strokeAccept };
                change = CR.recolor(desc.getObjectValue(CR.s("AGMStrokeStyleInfo")), "stroke", ctx);
                if (ctx.changes.length) {
                    var style = new ActionDescriptor();
                    style.putObject(CR.s("strokeStyle"), CR.s("strokeStyle"), change);
                    set = new ActionDescriptor();
                    set.putReference(CR.c("null"), CR.targetRef(CR.s("contentLayer")));
                    set.putObject(CR.c("T   "), CR.s("shapeStyle"), style);
                    executeAction(CR.c("setd"), set, DialogModes.NO);
                    done = done.concat(ctx.changes);
                }
            } catch (e2) {
                job.errors.push({ id: layer.id, part: "stroke", message: String(e2.message || e2) });
            }
        }
        if (hits.text && desc.hasKey(CR.s("textKey"))) {
            try {
                CR.applyText(layer, desc.getObjectValue(CR.s("textKey")), map, done);
            } catch (e3) {
                job.errors.push({ id: layer.id, part: "text", message: String(e3.message || e3) });
            }
        }
        if (hits.fx && desc.hasKey(CR.s("layerEffects"))) {
            try {
                ctx = { map: map, changes: [], accept: null };
                change = CR.recolor(desc.getObjectValue(CR.s("layerEffects")), "fx", ctx);
                if (ctx.changes.length) {
                    var ref = new ActionReference();
                    ref.putProperty(CR.c("Prpr"), CR.c("Lefx"));
                    ref.putEnumerated(CR.c("Lyr "), CR.c("Ordn"), CR.c("Trgt"));
                    set = new ActionDescriptor();
                    set.putReference(CR.c("null"), ref);
                    set.putObject(CR.c("T   "), CR.c("Lefx"), change);
                    executeAction(CR.c("setd"), set, DialogModes.NO);
                    done = done.concat(ctx.changes);
                }
            } catch (e4) {
                job.errors.push({ id: layer.id, part: "effects", message: String(e4.message || e4) });
            }
        }
    } finally {
        if (relock) {
            try {
                CR.setLocking(relock);
            } catch (e5) {
                job.errors.push({ id: layer.id, part: "lock", message: "Colorroom could not lock this layer again: " + e5.message });
            }
        }
    }
    for (j = 0; j < done.length; j++) {
        done[j].id = layer.id;
        job.changes.push(done[j]);
    }
};

// Text: one color for the whole layer goes through the plain text color (the DOM), which
// touches nothing else. Several colors go through the text's style runs, written back whole.
CR.applyText = function (layer, textKey, map, done) {
    var ctx = { map: map, changes: [], accept: CR.textAccept }, runs, copy, set, i, colors = {}, count = 0, hex;
    runs = CR.recolorList(textKey.getList(CR.s("textStyleRange")), "text/textStyleRange", ctx);
    if (!ctx.changes.length) {
        return;
    }
    for (i = 0; i < layer.parts.length; i++) {
        if (layer.parts[i].key.indexOf("text/") === 0 && !colors[layer.parts[i].hex]) {
            colors[layer.parts[i].hex] = true;
            count += 1;
            hex = layer.parts[i].hex;
        }
    }
    if (count === 1) {
        app.activeDocument.activeLayer.textItem.color = CR.solid(map[hex]);
    } else {
        copy = CR.recolor(textKey, "text", { map: {}, changes: [], accept: null });
        copy.putList(CR.s("textStyleRange"), runs);
        set = new ActionDescriptor();
        set.putReference(CR.c("null"), CR.targetRef(CR.c("TxLr")));
        set.putObject(CR.c("T   "), CR.c("TxLr"), copy);
        executeAction(CR.c("setd"), set, DialogModes.NO);
    }
    for (i = 0; i < ctx.changes.length; i++) {
        done.push(ctx.changes[i]);
    }
};

CR.applyAll = function () {
    var job = CR.job, i;
    for (i = 0; i < job.before.layers.length; i++) {
        try {
            CR.applyLayer(job.before.layers[i], job);
        } catch (e) {
            job.errors.push({ id: job.before.layers[i].id, part: "layer", message: String(e.message || e) });
        }
    }
};

CR.run(function (args) {
    var doc, before, key, map = {};
    if (CR.findOpen(args.path)) {
        throw CR.fail("already-open", "A file with the export's name is already open in Photoshop. Close it there and try again.");
    }
    for (key in args.map) {
        if (args.map.hasOwnProperty(key)) {
            map[String(key).toUpperCase()] = String(args.map[key]).toUpperCase();
        }
    }
    doc = CR.open(args.path);
    try {
        before = CR.manifest(doc);
        if (before.color_mode !== "RGB") {
            throw CR.fail("mode", "This document is in " + before.color_mode + " mode. Colorroom can only change colors in RGB documents for now, so nothing was changed.");
        }
        if (before.bit_depth === 32) {
            throw CR.fail("bits", "This document uses 32 bits per channel. Colorroom can only change 8 and 16 bit documents for now, so nothing was changed.");
        }
        CR.job = { map: map, hidden: args.hidden !== false, locked: args.locked === true, before: before, changes: [], skipped: [], errors: [] };
        doc.suspendHistory("Colorroom colors", "CR.applyAll()");
        doc.save();
    } finally {
        doc.close(SaveOptions.DONOTSAVECHANGES);
    }
    return { before: before, changes: CR.job.changes, skipped: CR.job.skipped, errors: CR.job.errors, saved: true };
});
