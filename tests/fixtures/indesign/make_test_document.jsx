// Made by Opus 5.5. Colorroom tests: build the InDesign test document inside InDesign.
//
// Arguments (CR_ARGS): {indd: "…/colorroom-indesign-test.indd", idml: "…/colorroom-indesign-test.idml"}
// Two pages, two layers, swatches (RGB, CMYK, spot, a gradient stop color), a tint, a gradient,
// rectangles with fill and stroke, an oval, a text frame with two colored words, a locked item,
// a purple box and an unused swatch. Saves the .indd and exports an .idml, then closes.
//@include "../../../src/colorroom/core/bridges/scripts/indesign_lib.jsx"

function main(args) {
    var doc = app.documents.add(false);
    CR.opened.push(doc);
    var prefs = doc.documentPreferences;
    prefs.intent = DocumentIntentOptions.PRINT_INTENT;
    prefs.facingPages = false;
    prefs.pagesPerDocument = 2;
    prefs.pageWidth = 400;
    prefs.pageHeight = 300;

    var back = doc.layers[0];
    back.name = "Background";
    var art = doc.layers.add({name: "Artwork"});

    var blue = doc.colors.add({name: "CR RGB Blue", model: ColorModel.PROCESS, space: ColorSpace.RGB, colorValue: [29, 78, 216]});
    var red = doc.colors.add({name: "CR CMYK Red", model: ColorModel.PROCESS, space: ColorSpace.CMYK, colorValue: [0, 90, 85, 0]});
    var green = doc.colors.add({name: "CR Spot Green", model: ColorModel.SPOT, space: ColorSpace.CMYK, colorValue: [80, 0, 90, 0]});
    var orange = doc.colors.add({name: "CR Stop Orange", model: ColorModel.PROCESS, space: ColorSpace.RGB, colorValue: [249, 115, 22]});
    var tint = doc.tints.add(blue, {tintValue: 40});
    var gradient = doc.gradients.add({name: "CR Gradient", type: GradientType.LINEAR});
    gradient.gradientStops[0].stopColor = orange;
    gradient.gradientStops[1].stopColor = blue;

    var page1 = doc.pages[0], page2 = doc.pages[1];
    page1.rectangles.add(back, LocationOptions.AT_END, page1, {
        name: "Backdrop", geometricBounds: [0, 0, 300, 400], fillColor: tint, strokeColor: doc.swatches.itemByName("None")
    });
    page1.rectangles.add(art, LocationOptions.AT_END, page1, {
        name: "Blue box", geometricBounds: [20, 20, 120, 180], fillColor: blue, strokeColor: red, strokeWeight: 6
    });
    page1.rectangles.add(art, LocationOptions.AT_END, page1, {
        name: "Gradient box", geometricBounds: [20, 200, 120, 380], fillColor: gradient, strokeColor: green, strokeWeight: 3
    });
    var words = page1.textFrames.add(art, LocationOptions.AT_END, page1, {
        name: "Two words", geometricBounds: [150, 20, 230, 380], contents: "Blue Red"
    });
    words.texts[0].pointSize = 40;
    words.words[0].fillColor = blue;
    words.words[1].fillColor = red;

    page2.ovals.add(art, LocationOptions.AT_END, page2, {
        name: "Green dot", geometricBounds: [40, 40, 140, 140], fillColor: green, strokeColor: doc.swatches.itemByName("Black"), strokeWeight: 1
    });
    var lockedBox = page2.rectangles.add(art, LocationOptions.AT_END, page2, {
        name: "Locked box", geometricBounds: [40, 200, 140, 360], fillColor: red, strokeColor: blue, strokeWeight: 2
    });
    lockedBox.locked = true;

    // The tests turn "CR Purple" into an unnamed color through IDML (InDesign's scripting
    // refuses to make one: "Swatch must have a name").
    var purple = doc.colors.add({name: "CR Purple", model: ColorModel.PROCESS, space: ColorSpace.RGB, colorValue: [120, 30, 160]});
    page2.rectangles.add(art, LocationOptions.AT_END, page2, {
        name: "Purple box", geometricBounds: [180, 40, 260, 360], fillColor: purple, strokeColor: doc.swatches.itemByName("None")
    });
    doc.colors.add({name: "CR Unused Teal", model: ColorModel.PROCESS, space: ColorSpace.RGB, colorValue: [13, 148, 136]});

    doc.save(File(args.indd));
    doc.exportFile(ExportFormat.INDESIGN_MARKUP, File(args.idml), false);
    return {indd: File(args.indd).fsName, idml: File(args.idml).fsName};
}

CR.run(main);
