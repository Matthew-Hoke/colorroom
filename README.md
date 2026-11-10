# Colorroom

Colorroom is an offline image palette studio. Drop in an image to find its exact HEX colors, see where they appear, and build a palette for your next design.

## Features

- **Image colors** shows distinct HEX values, pixel counts, and each color’s share of the visible image. Search by HEX, adjust how many shades to browse, or export the full list as CSV.
- **Color markers** highlight a sampled pixel when you hover over a swatch. Click a swatch to explore related shades from the image.
- **Design palettes** include image-based, color wheel, and themed styles. Pick your own colors for a custom palette, then copy the HEX codes or export CSS variables.

## Getting started

1. Download or clone this repository.
2. Open `index.html` in Microsoft Edge or another modern browser. A sample image is ready to explore.
3. Drop in an image or select **Choose image**.

Colorroom supports PNG, JPEG, WebP, GIF, BMP, and AVIF images up to 24 megapixels. Animated images are analyzed using their first frame.

## Privacy and color matching

Image processing runs in your browser. Colorroom uses the browser’s sRGB pixel values and skips fully transparent pixels. Creative palette styles can suggest colors beyond the source image. When you hover over one of those suggestions, Colorroom labels the nearby sampled HEX that its marker points to.

## Tech stack

Colorroom is a standalone HTML, CSS, and JavaScript app. It uses Canvas and a Web Worker for local image analysis. Open the HTML file directly to run it.
