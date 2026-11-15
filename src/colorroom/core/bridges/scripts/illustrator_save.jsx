// Made by Opus 5.5. Colorroom, Illustrator bridge: save an open document as .ai, then close it.
// Arguments (CR_ARGS): path (the open document, a copy Colorroom made), close (true to close
// it after saving). Saves in place with PDF compatibility on, so other programs (and
// Colorroom's own reader) can still see the artwork. Answers with the document info.
//@include "illustrator_lib.jsx"

CR.run(function (args) {
    var doc = CR.findDocument(args.path), options, info;
    if (!args.path || !doc) {
        throw new Error("The copy to save is not open in Illustrator.");
    }
    options = new IllustratorSaveOptions();
    options.pdfCompatible = true;
    options.embedICCProfile = true;
    options.embedLinkedFiles = false;
    options.saveMultipleArtboards = false;
    doc.saveAs(new File(args.path), options);
    info = CR.documentInfo(doc);
    if (args.close) {
        doc.close(SaveOptions.DONOTSAVECHANGES);
    }
    return { document: info, closed: !!args.close };
});
