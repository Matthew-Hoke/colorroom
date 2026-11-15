// Made by Opus 5.5. Colorroom, InDesign bridge: change swatch colors in a copy and save it.
//
// Arguments (CR_ARGS):
//   open  the file to open: Colorroom's copy of the .indd, or an .idml (never the original .indd)
//   save  where to save the .indd (the copy itself, or the new .indd for an .idml)
//   idml  optional: also export the result as IDML here
//   plan  [{id, name, space: "RGB"|"CMYK"|"LAB", rgb: [r, g, b]} or {id, name, space, values: [...]}]
//         rgb: set the color as RGB, then let InDesign convert it to `space` with the document's
//         own color profile. values: set these exact values in `space`.
// Changing a swatch recolors everything that uses it (frames, strokes, text, tints, gradient
// stops), the way editing a swatch does in InDesign. All changes are one undo step.
//@include "indesign_lib.jsx"

var CR_SPACES = {RGB: ColorSpace.RGB, CMYK: ColorSpace.CMYK, LAB: ColorSpace.LAB};

function changeOne(doc, step) {
    var out = {id: String(step.id), ok: false};
    var color = doc.colors.itemByID(Number(step.id));
    var target = CR_SPACES[step.space];
    try {
        if (!color.isValid) {
            out.error = "The color is no longer in the document.";
            return out;
        }
        if (CR.get(color, "colorEditable", true) === false) {
            out.error = "InDesign does not allow this color to be edited.";
            return out;
        }
        if (!target) {
            out.error = "Unknown color space " + step.space + ".";
            return out;
        }
        out.spaceBefore = CR.enumName(color.space);
        out.valuesBefore = CR.values(color.colorValue);
        if (step.rgb) {
            color.space = ColorSpace.RGB;
            color.colorValue = step.rgb;
            if (target !== ColorSpace.RGB) {
                color.space = target;
            }
        } else {
            color.space = target;
            color.colorValue = step.values;
        }
        out.space = CR.enumName(color.space);
        out.values = CR.values(color.colorValue);
        out.model = CR.enumName(color.model);
        out.ok = true;
    } catch (e) {
        out.error = String(e.message || e);
    }
    return out;
}

function main(args) {
    var found = CR.document(args.open);
    if (!found.ours) {
        throw new Error("IN_USE: This copy is already open in InDesign. Close it there and try again.");
    }
    var doc = found.doc, results = [], i;
    app.doScript(function () {
        for (i = 0; i < args.plan.length; i++) {
            results.push(changeOne(doc, args.plan[i]));
        }
    }, ScriptLanguage.JAVASCRIPT, [], UndoModes.ENTIRE_SCRIPT, "Colorroom colors");
    doc.save(File(args.save));
    var idml = "";
    if (args.idml) {
        doc.exportFile(ExportFormat.INDESIGN_MARKUP, File(args.idml), false);
        idml = File(args.idml).fsName;
    }
    return {results: results, saved: File(args.save).fsName, idml: idml};
}

CR.run(main);
