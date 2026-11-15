// Made by Opus 5.5. Colorroom, Illustrator bridge: open one file that is not open yet.
// Arguments (CR_ARGS): path. Refuses when that file is already open, so Colorroom never takes
// over a document the user is working on. Answers with the document info.
//@include "illustrator_lib.jsx"

CR.run(function (args) {
    var file = new File(args.path), doc;
    if (!file.exists) {
        throw new Error("The file to open does not exist.");
    }
    if (CR.findDocument(args.path)) {
        throw new Error("That file is already open in Illustrator.");
    }
    doc = app.open(file);
    return { document: CR.documentInfo(doc) };
});
