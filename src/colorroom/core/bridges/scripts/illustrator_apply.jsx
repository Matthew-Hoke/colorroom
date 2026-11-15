// Made by Opus 5.5. Colorroom, Illustrator bridge: apply a color mapping to an open document.
//
// Colorroom only sends this to a COPY it opened itself. It never saves; illustrator_save.jsx
// does that. Arguments (CR_ARGS):
//   path            the open document (required, so the user's own documents are never hit)
//   mapping         {"#RRGGBB": "#RRGGBB"}: the old hex as Colorroom read it -> the new hex
//   includeLocked   also change locked objects (and objects on locked layers)
//   includeHidden   also change hidden objects (and objects on hidden layers)
//   includeMissingFonts  also change text whose font is not installed
//   swatches        also change plain (non-global) swatches of a mapped color, so the Swatches
//                   panel matches the new colors
// Global and spot swatches, and gradients, are changed in their definition, so everything that
// uses them follows, locked or not. The answer lists every change made and everything skipped.
//@include "illustrator_lib.jsx"

CR.run(function (args) {
    var doc = CR.findDocument(args.path), mapping = args.mapping || {}, targets = {}, made = {}, changes = [], skipped = [],
        notes = [], key, i, j, spot, base, gradient, stop, swatch, described;
    if (!args.path || !doc) {
        throw new Error("The copy to change is not open in Illustrator.");
    }

    function target(hex) {
        if (!targets.hasOwnProperty(hex)) {
            targets[hex] = CR.target(doc, mapping[hex]);
        }
        return targets[hex];
    }
    function newColor(hex) {
        return CR.make(target(hex));
    }
    function mapped(described) {
        return CR.isPlain(described) && described.hex && mapping.hasOwnProperty(described.hex);
    }

    // Unlocks and shows an object and its parents for one change, and puts every flag back.
    function withAccess(item, change) {
        var undo = [], node = item, k;
        try {
            return change();
        } catch (first) {
            while (node && node.typename !== "Document") {
                try {
                    if (node.locked) {
                        node.locked = false;
                        undo.push([node, "locked", true]);
                    }
                    if (node.typename === "Layer") {
                        if (!node.visible) {
                            node.visible = true;
                            undo.push([node, "visible", false]);
                        }
                    } else if (node.hidden) {
                        node.hidden = false;
                        undo.push([node, "hidden", true]);
                    }
                } catch (ignore) {}
                node = node.parent;
            }
            if (undo.length === 0) {
                throw first;
            }
            try {
                return change();
            } finally {
                for (k = undo.length - 1; k >= 0; k--) {
                    undo[k][0][undo[k][1]] = undo[k][2];
                }
            }
        }
    }

    // 1. Global and spot swatches: change the definition once.
    for (i = 0; i < doc.spots.length; i++) {
        spot = doc.spots[i];
        if (CR.spotKind(spot) === "registration") {
            continue;
        }
        base = CR.color(spot.color);
        if (mapped(base)) {
            spot.color = newColor(base.hex);
            changes.push({ slot: CR.spotKind(spot) + " swatch", name: spot.name, from: base.hex, to: target(base.hex).hex });
        }
    }

    // 2. Gradients: change the stops of the gradient itself (objects that use it follow).
    for (i = 0; i < doc.gradients.length; i++) {
        gradient = doc.gradients[i];
        for (j = 0; j < gradient.gradientStops.length; j++) {
            stop = gradient.gradientStops[j];
            described = CR.color(stop.color);
            if (mapped(described)) {
                stop.color = newColor(described.hex);
                changes.push({ slot: "gradient stop", name: gradient.name, stop: j, from: described.hex, to: target(described.hex).hex });
            }
        }
    }

    // 3. Objects.
    function paintPath(paint, item, id, info) {
        var fill = paint.filled ? CR.color(paint.fillColor) : null,
            stroke = paint.stroked ? CR.color(paint.strokeColor) : null, parts, p;
        parts = item.typename === "CompoundPathItem" ? item.pathItems : [paint];
        if (mapped(fill)) {
            withAccess(item, function () {
                for (p = 0; p < parts.length; p++) {
                    parts[p].fillColor = newColor(fill.hex);
                }
            });
            changes.push({ id: id, name: info.name, layer: info.layer, slot: "fill", from: fill.hex, to: target(fill.hex).hex });
        }
        if (mapped(stroke)) {
            withAccess(item, function () {
                for (p = 0; p < parts.length; p++) {
                    parts[p].strokeColor = newColor(stroke.hex);
                }
            });
            changes.push({ id: id, name: info.name, layer: info.layer, slot: "stroke", from: stroke.hex, to: target(stroke.hex).hex });
        }
    }

    function paintText(frame, id, info) {
        var runs = CR.runs(frame), r, run, range, slot, color;
        function rangeOf(run) {
            var piece = frame.characters[run.start];
            piece.length = run.length;
            return piece;
        }
        for (r = 0; r < runs.length; r++) {
            run = runs[r];
            for (slot = 0; slot < 2; slot++) {
                color = slot === 0 ? run.fill : run.stroke;
                if (!mapped(color)) {
                    continue;
                }
                withAccess(frame, function () {
                    range = rangeOf(run);
                    if (slot === 0) {
                        range.characterAttributes.fillColor = newColor(color.hex);
                    } else {
                        range.characterAttributes.strokeColor = newColor(color.hex);
                    }
                });
                changes.push({ id: id, name: info.name, layer: info.layer, slot: slot === 0 ? "text fill" : "text stroke",
                    start: run.start, length: run.length, from: color.hex, to: target(color.hex).hex });
            }
        }
    }

    function uses(info) {
        // The mapped colors this object uses directly (not through a swatch or a gradient).
        var found = [], r;
        if (mapped(info.fill)) {
            found.push(info.fill.hex);
        }
        if (mapped(info.stroke)) {
            found.push(info.stroke.hex);
        }
        for (r = 0; info.runs && r < info.runs.length; r++) {
            if (mapped(info.runs[r].fill)) {
                found.push(info.runs[r].fill.hex);
            }
            if (mapped(info.runs[r].stroke)) {
                found.push(info.runs[r].stroke.hex);
            }
        }
        return found;
    }

    CR.walk(doc, {
        item: function (item, id, path, locked, hidden) {
            var info, wanted, reason = "", paint;
            if (item.typename !== "PathItem" && item.typename !== "CompoundPathItem" && item.typename !== "TextFrame") {
                return; // groups are walked into; pictures, symbols, meshes and the rest are left alone
            }
            info = CR.describeItem(item, id, path, locked, hidden);
            wanted = uses(info);
            if (wanted.length === 0) {
                return;
            }
            if (locked && !args.includeLocked) {
                reason = "locked";
            } else if (hidden && !args.includeHidden) {
                reason = "hidden";
            } else if (info.kind === "guide") {
                reason = "guide";
            } else if (info.missingFonts && info.missingFonts.length && !args.includeMissingFonts) {
                reason = "missing font: " + info.missingFonts.join(", ");
            } else if (info.kind === "compound" && info.paths === 0) {
                reason = "compound path whose parts scripts cannot reach";
            }
            if (reason) {
                skipped.push({ id: id, name: info.name, layer: path, reason: reason, colors: wanted });
                return;
            }
            try {
                if (item.typename === "TextFrame") {
                    paintText(item, id, info);
                } else {
                    paint = CR.paintOf(item);
                    paintPath(paint, item, id, info);
                }
            } catch (error) {
                skipped.push({ id: id, name: info.name, layer: path, reason: "Illustrator refused: " + (error.message || error), colors: wanted });
            }
        }
    });

    // 4. Plain swatches, so the Swatches panel shows the new colors too.
    if (args.swatches) {
        for (i = 0; i < doc.swatches.length; i++) {
            swatch = doc.swatches[i];
            try {
                described = CR.color(swatch.color);
            } catch (error2) {
                continue;
            }
            if (mapped(described)) {
                swatch.color = newColor(described.hex);
                changes.push({ slot: "swatch", name: swatch.name, from: described.hex, to: target(described.hex).hex });
            }
        }
    }

    for (key in mapping) {
        if (mapping.hasOwnProperty(key)) {
            target(key);
        }
    }
    return { changes: changes, skipped: skipped, targets: targets, colorMode: CR.documentInfo(doc).colorMode, notes: notes };
});
