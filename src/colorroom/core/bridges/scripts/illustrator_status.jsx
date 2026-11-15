// Made by Opus 5.5. Colorroom, Illustrator bridge: what is Illustrator doing right now?
// No arguments. Answers with the version and every open document (name, path, saved), the
// active one first. Changes nothing.
//@include "illustrator_lib.jsx"

CR.run(function () {
    var documents = [], i, doc, active = "";
    if (app.documents.length > 0) {
        active = CR.docPath(app.activeDocument) || app.activeDocument.name;
    }
    for (i = 0; i < app.documents.length; i++) {
        doc = app.documents[i];
        documents.push({ name: doc.name, path: CR.docPath(doc), saved: doc.saved,
            colorMode: doc.documentColorSpace === DocumentColorSpace.CMYK ? "CMYK" : "RGB" });
    }
    return { version: app.version, documents: documents, active: active };
});
