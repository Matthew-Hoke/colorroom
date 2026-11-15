# Colorroom FABLE roadmap

Colorroom's main job: **open your own design file, try palettes on it, and get the same file
back with the new colors on its real layers, ready to edit.** This page says what works today,
what does not yet, and what comes next. It is updated with every release.

## What works in 3.0

| Your file | What you get back | How |
|---|---|---|
| Photoshop (`.psd`) with Photoshop installed | The same PSD with new colors on text, shapes, fill layers, gradient maps, gradients and layer effects. Every layer, the size, the resolution and the bit depth are kept | Colorroom asks your Photoshop to make the change and checks every color in the saved file |
| Photoshop (`.psd`) without Photoshop | The same PSD. Pixel layers are recolored in place; when colors come from text, shapes or adjustments, the new colors go on one extra layer on top and your layers stay untouched | Colorroom writes the file itself and checks it |
| Illustrator (`.ai`) with Illustrator installed | The same `.ai`, still vectors, with the exact HEX on fills, strokes, text, gradients, global and spot swatches | Your Illustrator makes the change; Colorroom checks it |
| InDesign (`.indd`, `.idml`) with InDesign installed | The same document with its swatches changed, so every frame, text run, tint and gradient follows | Your InDesign makes the change; Colorroom checks it |
| InDesign markup (`.idml`) without InDesign | The same IDML with new swatch values; InDesign opens it fully editable | Colorroom rewrites only the swatch values |
| PNG, JPEG, TIFF, WebP, GIF, BMP, AVIF, PDF | The same format at full size and resolution, or another format if you choose | Colorroom writes it |

Your original file is never changed: the result is saved beside it as `name - Colorroom`.
Everything runs on your computer.

## Known limits

- **The preview is close, the export is exact.** The preview recolors a picture of your file;
  the exported file has the exact colors on its layers. Photos under adjustment layers can look
  different in the preview than in the export.
- Photoshop: pixel layers and smart object contents are not changed by the Photoshop route;
  CMYK and 32-bit documents are not supported yet.
- Illustrator: extra fills and strokes from the Appearance panel, effects, graphic styles,
  symbols and patterns keep their colors.
- InDesign: colors are changed through swatches. In print (CMYK) documents InDesign converts the
  new color with the document's profile, so it is close to, not exactly, the HEX you chose; the
  report lists each one.
- Without Illustrator, an `.ai` file comes back as a PDF with the artwork as one picture.
- Windows only for now. The app is not signed, so Windows asks before the first start.
- Linking to the Adobe programs needs Windows and the program installed on the same computer.

## Next

1. **A preview drawn by the program itself**: an exact "Preview in Photoshop" (and Illustrator,
   InDesign) before you export.
2. **The exact colors of your file in the color table**, read from the program instead of
   guessed from pixels, with what uses each color ("12 layers", "309 text runs").
3. **Better automatic roles**: a logo on a transparent background keeps its main color as the
   main color; text that would become hard to read is flagged.
4. **Smoother recoloring of shaded artwork** (pencil, gradients) by keeping the order and spacing
   of shades.
5. **Options A, B, C**: try several versions of the same file and compare them side by side.
6. **Figma**: a plugin that changes fills, strokes and text in your Figma file.
7. **macOS**, a signed Windows app, and an installer.
