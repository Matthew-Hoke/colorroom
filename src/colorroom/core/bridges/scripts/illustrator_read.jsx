// Made by Opus 5.5. Colorroom, Illustrator bridge: read one document.
//
// Arguments (CR_ARGS): path (optional; empty means the active document), open (true: open
// the file if it is not open, and close it again afterwards), preview (optional PNG path: a
// picture of the first artboard). Answers with the document snapshot (see CR.snapshot).
//@include "illustrator_lib.jsx"

CR.run(function (args) {
    var doc = CR.findDocument(args.path), openedHere = false, info, options;
    if (!doc) {
        if (!args.open) {
            throw new Error("That document is not open in Illustrator.");
        }
        doc = app.open(new File(args.path));
        openedHere = true;
    }
    try {
        info = CR.snapshot(doc);
        if (args.preview) {
            var board = doc.artboards.getActiveArtboardIndex();
            options = new ExportOptionsPNG24();
            options.artBoardClipping = true;
            options.antiAliasing = true;
            options.transparency = false;
            options.horizontalScale = args.scale || 100;
            options.verticalScale = args.scale || 100;
            doc.artboards.setActiveArtboardIndex(0);
            doc.exportFile(new File(args.preview), ExportType.PNG24, options);
            doc.artboards.setActiveArtboardIndex(board);
            info.preview = args.preview;
        }
    } finally {
        if (openedHere) {
            doc.close(SaveOptions.DONOTSAVECHANGES);
        }
    }
    info.openedHere = openedHere;
    return info;
});
