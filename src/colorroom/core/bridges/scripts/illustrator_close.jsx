// Made by Opus 5.5. Colorroom, Illustrator bridge: close a document Colorroom opened, without
// saving. Arguments (CR_ARGS): path. Used to clean up after a failed apply.
//@include "illustrator_lib.jsx"

CR.run(function (args) {
    var doc = CR.findDocument(args.path);
    if (args.path && doc) {
        doc.close(SaveOptions.DONOTSAVECHANGES);
        return { closed: true };
    }
    return { closed: false };
});
