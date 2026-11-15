//@include "photoshop_lib.jsx"
// Made by Opus 5.5 (2026-11-14). Colorroom, Photoshop bridge: read one document.
// One job: list every layer with its exact colors, and save a preview picture. Never changes
// the document. A document this script opened is closed again unless leave_open is true; a
// document that was already open stays open and untouched.
// CR_ARGS: { path: "C:/.../file.psd", or "" for the document in front,
//            preview: "C:/.../name.colorroom-preview.png", or "" for none,
//            leave_open: false }

CR.run(function (args, state) {
    var doc = null, opened = false, result;
    if (args.path) {
        doc = CR.findOpen(args.path);
        if (!doc) {
            doc = CR.open(args.path);
            opened = true;
        }
    } else {
        if (!app.documents.length) {
            throw CR.fail("no-document", "There is no document open in Photoshop.");
        }
        doc = app.activeDocument;
    }
    try {
        result = CR.manifest(doc);
        result.was_open = !opened;
        if (args.preview) {
            try {
                CR.savePreview(doc, args.preview);
                result.preview_file = args.preview;
            } catch (e) {
                result.notes.push("Photoshop could not make the preview picture: " + e.message);
            }
        }
    } finally {
        if (opened && !args.leave_open) {
            doc.close(SaveOptions.DONOTSAVECHANGES);
        }
    }
    if (opened && args.leave_open) {
        state.active = doc;
    }
    return result;
});
