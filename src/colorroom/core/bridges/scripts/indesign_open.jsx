// Made by Opus 5.5. Colorroom, InDesign bridge: show a file in InDesign, ready to edit.
//
// Arguments (CR_ARGS): {path: the .indd Colorroom saved}
// Opens it in a window (or brings an open one forward). Nothing is changed or saved.
//@include "indesign_lib.jsx"

function main(args) {
    var doc = CR.findOpen(args.path);
    if (!doc) {
        if (!File(args.path).exists) {
            throw new Error("NO_FILE: The file could not be found.");
        }
        doc = app.open(File(args.path), true);
        if (doc instanceof Array) {
            doc = doc[0];
        }
    } else if (doc.windows.length === 0) {
        doc.windows.add();
    }
    try { app.activeDocument = doc; } catch (e) {}
    return {document: String(doc.name)};
}

CR.run(main);
