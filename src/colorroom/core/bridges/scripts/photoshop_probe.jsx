//@include "photoshop_lib.jsx"
// Made by Opus 5.5 (2026-11-14). Colorroom, Photoshop bridge: what is open right now.
// One job: report Photoshop's version and its open documents. Changes nothing.
// CR_ARGS: {} (none)

CR.run(function (args) {
    var docs = [], i, doc, active = null;
    try {
        active = app.documents.length ? app.activeDocument : null;
    } catch (e) {}
    for (i = 0; i < app.documents.length; i++) {
        doc = app.documents[i];
        docs.push({ name: doc.name, path: CR.docPath(doc), saved: doc.saved, active: active !== null && active.name === doc.name && CR.docPath(active) === CR.docPath(doc) });
    }
    return { program: "Photoshop", version: String(app.version), documents: docs };
});
