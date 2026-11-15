// Made by Opus 5.5. Colorroom, InDesign bridge: export one page as a PNG or JPEG picture.
//
// Arguments (CR_ARGS): {path: .indd/.idml or "" for the active document, out: ".../page.png",
//                       page: 1-based page number, resolution: pixels per inch (72 = 1 px per point)}
// The document is not changed. InDesign's own export settings are put back afterwards.
//@include "indesign_lib.jsx"

function main(args) {
    var found = CR.document(args.path || "");
    var doc = found.doc;
    var number = Number(args.page || 1);
    var out = File(args.out);
    var jpeg = /\.jpe?g$/i.test(out.name);
    if (number < 1 || number > doc.pages.length || number !== Math.floor(number)) {
        throw new Error("NO_PAGE: The document has " + doc.pages.length + " pages; page " + args.page + " does not exist.");
    }
    var prefs = jpeg ? app.jpegExportPreferences : app.pngExportPreferences;
    var names = jpeg
        ? ["jpegExportRange", "pageString", "exportResolution", "jpegQuality", "jpegColorSpace", "exportingSpread", "antiAlias", "useDocumentBleeds", "simulateOverprint"]
        : ["pngExportRange", "pageString", "exportResolution", "pngQuality", "pngColorSpace", "exportingSpread", "antiAlias", "useDocumentBleeds", "simulateOverprint", "transparentBackground"];
    var saved = {}, i;
    for (i = 0; i < names.length; i++) {
        try { saved[names[i]] = prefs[names[i]]; } catch (e) {}
    }
    try {
        if (jpeg) {
            prefs.jpegExportRange = ExportRangeOrAllPages.EXPORT_RANGE;
            prefs.jpegQuality = JPEGOptionsQuality.MAXIMUM;
            try { prefs.jpegColorSpace = JpegColorSpaceEnum.RGB; } catch (e1) {}
        } else {
            prefs.pngExportRange = PNGExportRangeEnum.EXPORT_RANGE;
            prefs.pngQuality = PNGQualityEnum.MAXIMUM;
            try { prefs.pngColorSpace = PNGColorSpaceEnum.RGB; } catch (e2) {}
            prefs.transparentBackground = false;
        }
        prefs.pageString = "+" + number; // "+n" is the nth page, whatever the page numbering
        prefs.exportResolution = Number(args.resolution || 72);
        prefs.exportingSpread = false;
        prefs.antiAlias = true;
        prefs.useDocumentBleeds = false;
        try { prefs.simulateOverprint = false; } catch (e3) {}
        doc.exportFile(jpeg ? ExportFormat.JPG : ExportFormat.PNG_FORMAT, out, false);
    } finally {
        for (i = 0; i < names.length; i++) {
            if (saved.hasOwnProperty(names[i])) {
                try { prefs[names[i]] = saved[names[i]]; } catch (e4) {}
            }
        }
    }
    var bounds = doc.pages[number - 1].bounds; // [top, left, bottom, right] in points
    return {
        out: out.fsName, exists: out.exists, page: number, pages: doc.pages.length,
        pageWidth: Number(bounds[3] - bounds[1]), pageHeight: Number(bounds[2] - bounds[0]),
        resolution: Number(args.resolution || 72)
    };
}

CR.run(main);
