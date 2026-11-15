// Made by Opus 5.5. Colorroom, InDesign bridge: which documents are open. Changes nothing.
//
// Arguments: none. Returns {version, documents: [{name, path, saved, modified, active}]}.
//@include "indesign_lib.jsx"

function main(args) {
    var out = [], i, doc, active = null;
    try { active = app.documents.length ? app.activeDocument : null; } catch (e) {}
    for (i = 0; i < app.documents.length; i++) {
        doc = app.documents[i];
        out.push({
            name: String(doc.name),
            path: doc.saved ? File(doc.fullName).fsName : "",
            saved: doc.saved === true,
            modified: CR.get(doc, "modified", false) === true,
            active: active !== null && doc.id === active.id
        });
    }
    return {version: String(app.version), documents: out};
}

CR.run(main);
