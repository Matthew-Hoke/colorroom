// Phase 0: lock the behavior of Colorroom 1.7.
//
// Runs the ORIGINAL JavaScript from legacy/index.html under Node (no browser needed) on
// the test images and saves what it produces to tests/fixtures/expected/*.json. The
// Python tests then check that the port gives the same answers.
//
// Run from the project folder:   node tools/make_fixtures.mjs

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import zlib from 'node:zlib';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const imageDir = path.join(root, 'tests', 'fixtures', 'images');
const outDir = path.join(root, 'tests', 'fixtures', 'expected');
const html = fs.readFileSync(path.join(root, 'legacy', 'index.html'), 'utf8');
const workerCode = html.match(/<script id="worker-code" type="text\/plain">([\s\S]*?)<\/script>/)[1];
const pageCode = html.slice(html.lastIndexOf('<script>') + '<script>'.length, html.lastIndexOf('</script>'));

const HEAD = 500;
const sha1 = values => crypto.createHash('sha1').update(Buffer.from(Uint32Array.from(values).buffer)).digest('hex');
const summary = values => ({ count: values.length, head: Array.from(values.slice(0, HEAD)), sha1: sha1(values) });

// --- a minimal PNG reader (8-bit RGBA, not interlaced) --------------------------------
function decodePng(file) {
  const buffer = fs.readFileSync(file);
  let position = 8, width, height;
  const parts = [];
  while (position < buffer.length) {
    const length = buffer.readUInt32BE(position);
    const type = buffer.toString('latin1', position + 4, position + 8);
    const data = buffer.subarray(position + 8, position + 8 + length);
    if (type === 'IHDR') {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      if (data[8] !== 8 || data[9] !== 6 || data[12] !== 0) throw new Error(`${file}: expected 8-bit RGBA, not interlaced`);
    } else if (type === 'IDAT') parts.push(data);
    else if (type === 'IEND') break;
    position += 12 + length;
  }
  const raw = zlib.inflateSync(Buffer.concat(parts));
  const stride = width * 4;
  const pixels = new Uint8Array(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    const current = pixels.subarray(y * stride, (y + 1) * stride);
    const previous = y ? pixels.subarray((y - 1) * stride, y * stride) : null;
    for (let x = 0; x < stride; x++) {
      const left = x >= 4 ? current[x - 4] : 0;
      const up = previous ? previous[x] : 0;
      const upLeft = previous && x >= 4 ? previous[x - 4] : 0;
      let value = line[x];
      if (filter === 1) value += left;
      else if (filter === 2) value += up;
      else if (filter === 3) value += (left + up) >> 1;
      else if (filter === 4) {
        const estimate = left + up - upLeft;
        const dLeft = Math.abs(estimate - left), dUp = Math.abs(estimate - up), dUpLeft = Math.abs(estimate - upLeft);
        value += dLeft <= dUp && dLeft <= dUpLeft ? left : dUp <= dUpLeft ? up : upLeft;
      }
      current[x] = value & 255;
    }
  }
  return { width, height, data: pixels };
}

// --- run the original worker code with a stand-in for the browser canvas -------------
function createWorker(image) {
  const messages = [];
  class FakeContext {
    constructor(canvas) { this.canvas = canvas; this.pixels = null; }
    drawImage(bitmap, ...rest) { if (rest.length === 2) this.pixels = bitmap.data; }
    getImageData(x, y, w, h) {
      const data = new Uint8ClampedArray(w * h * 4);
      const width = this.canvas.width;
      for (let row = 0; row < h; row++) {
        const start = ((y + row) * width + x) * 4;
        data.set(this.pixels.subarray(start, start + w * 4), row * w * 4);
      }
      return { data };
    }
  }
  class OffscreenCanvas {
    constructor(width, height) { this.width = width; this.height = height; }
    getContext() { return this.context ??= new FakeContext(this); }
    convertToBlob() { return Promise.resolve(new Blob([])); }
  }
  const sandbox = {
    self: {}, Blob, console, OffscreenCanvas,
    postMessage: message => messages.push(message),
    createImageBitmap: async () => ({ width: image.width, height: image.height, data: image.data, close() {} })
  };
  const context = vm.createContext(sandbox);
  vm.runInContext(workerCode, context);
  return {
    async send(data) {
      messages.length = 0;
      await sandbox.self.onmessage({ data });
      return messages.slice();
    },
    evaluate: code => vm.runInContext(code, context)
  };
}

const hex = key => '#' + key.toString(16).padStart(6, '0').toUpperCase();
const unique = values => [...new Set(values)];

async function fixtureFor(name) {
  const image = decodePng(path.join(imageDir, `${name}.png`));
  const worker = createWorker(image);
  const [ready] = await worker.send({ type: 'image', file: {} });
  if (ready.type === 'error') return { error: ready.message };
  const n = ready.distinct;
  const out = { ready: { width: ready.width, height: ready.height, distinct: n, visiblePixels: ready.visiblePixels, base: ready.base } };

  const hexOrder = worker.evaluate('Array.from(hexOrder)');
  const frequencyOrder = worker.evaluate('Array.from(frequencyOrder)');
  const varietyOrder = worker.evaluate('Array.from(varietyOrder)');
  out.hexOrder = summary(hexOrder);
  out.frequencyOrder = summary(frequencyOrder);
  out.varietyOrder = summary(varietyOrder);
  out.counts = summary(worker.evaluate('Array.from(hexOrder, key => counts[key])'));
  out.locations = summary(worker.evaluate('Array.from(colorLocations)'));
  const candidates = worker.evaluate('candidates.map(candidate => [candidate.key, candidate.weight])');
  out.candidates = { count: candidates.length, head: candidates.slice(0, HEAD), sha1: sha1(candidates.map(c => c[0])) };
  out.balanced = {};
  for (const [limit, exponent] of [[3, .25], [6, .25], [12, .25], [12, .1]]) {
    out.balanced[`${limit}@${exponent}`] = worker.evaluate(`balanced(${limit}, ${exponent})`);
  }

  out.palettes = [];
  for (const size of [3, 6, 12]) {
    for (const mode of ['balanced', 'dominant']) {
      const [reply] = await worker.send({ type: 'palette', size, mode, request: 1 });
      out.palettes.push({ size, mode, entries: reply.entries });
    }
  }
  const probes = ['#FF0000', '#00FF00', '#0000FF', '#123456', '#FFFFFF', '#000000', '#808080', '#5A3CB0', '#F6C46B', '#37B4AE', ready.base];
  const [sources] = await worker.send({ type: 'palette-sources', colors: probes, roles: [], request: 1 });
  out.paletteSources = sources.entries;

  out.pixels = [];
  const limits = unique([n, Math.ceil(n / 2), Math.min(n, 13), 1]);
  for (const [x, y] of [[0, 0], [.5, .5], [.999, .999], [.25, .75], [.75, .2], [.1, .9], [.33, .66], [.9, .05]]) {
    for (const limit of limits) {
      const [reply] = await worker.send({ type: 'pixel', x, y, limit });
      out.pixels.push({ x, y, limit, pick: reply.pick });
    }
  }

  const bases = unique([frequencyOrder[0], varietyOrder[Math.min(1, n - 1)], varietyOrder[Math.min(5, n - 1)],
    varietyOrder[Math.min(40, n - 1)], frequencyOrder[n - 1], hexOrder[Math.floor(n / 2)], hexOrder[0], hexOrder[n - 1]]);
  out.inspect = [];
  for (const key of bases) {
    const color = hex(key);
    const all = worker.evaluate(`relatedColors(${key}).map(shade => [shade.key, shade.distance, shade.tone])`);
    const entry = { color, total: all.length, head: all.slice(0, HEAD), sha1: sha1(all.map(shade => shade[0])), pages: [], ranges: [] };
    for (const [tone, page] of [[null, 0], [null, 1], [0, 0], [1, 0], [2, 0], [3, 0], [4, 0], [2, 1]]) {
      const [reply] = await worker.send({ type: 'inspect', color, page, tone, request: 1 });
      entry.pages.push({ tone, page, point: reply.point, related: reply.related, tones: reply.tones, total: reply.total, size: reply.size });
    }
    for (const maximum of [0.1, 1, 8, 25, 99.9, 100]) {
      const [reply] = await worker.send({ type: 'shade-range', color, page: 0, maximum, request: 1 });
      entry.ranges.push({ maximum, shades: reply.shades, total: reply.total });
    }
    out.inspect.push(entry);
  }

  out.pages = [];
  const baseHex = ready.base.slice(1);
  const prefixes = unique(['', baseHex[0], baseHex.slice(0, 2), baseHex.slice(0, 4), baseHex, '0', 'F', 'ff', '8', '123456']);
  for (const prefix of prefixes) {
    for (const sort of ['variety', 'frequency', 'hex']) {
      for (const limit of limits) {
        for (const page of [0, 1]) {
          const [reply] = await worker.send({ type: 'page', prefix, sort, page, limit, request: 1 });
          out.pages.push({ prefix, sort, page, limit, total: reply.total, rows: reply.rows });
        }
      }
    }
  }

  const [csvReply] = await worker.send({ type: 'csv' });
  const csv = await csvReply.file.text();
  out.csv = { length: csv.length, sha1: crypto.createHash('sha1').update(csv, 'utf8').digest('hex'), head: csv.slice(0, 400) };
  return out;
}

// --- palette suggestions run on the page, not in the worker ---------------------------
function extractStatement(source, start) {
  // Returns source text from `start` to the end of that statement or function body.
  let depth = 0, quote = null, sawBrace = false;
  const isFunction = source.startsWith('function', start);
  for (let i = start; i < source.length; i++) {
    const ch = source[i];
    if (quote) {
      if (ch === '\\') i++;
      else if (ch === quote) quote = null;
      continue;
    }
    if (ch === '\'' || ch === '"' || ch === '`') quote = ch;
    else if (ch === '{' || ch === '(' || ch === '[') { depth++; sawBrace ||= ch === '{'; }
    else if (ch === '}' || ch === ')' || ch === ']') {
      depth--;
      if (isFunction && depth === 0 && ch === '}' && sawBrace) return source.slice(start, i + 1);
    } else if (ch === ';' && depth === 0 && !isFunction) return source.slice(start, i + 1);
  }
  throw new Error('Unterminated statement');
}

function pageLogic() {
  const pieces = [];
  for (const name of ['wheelHues', 'wheelStyles', 'aestheticStyles', 'themes', 'brandRoles', 'wrapHue', 'isImageMode']) {
    const start = pageCode.indexOf(`const ${name} = `);
    if (start < 0) throw new Error(`const ${name} not found`);
    pieces.push(extractStatement(pageCode, start).replace(/^const /, 'var '));
  }
  for (const name of ['toHsl', 'fromHsl', 'shiftOnWheel', 'suggestColors']) {
    const start = pageCode.indexOf(`function ${name}(`);
    if (start < 0) throw new Error(`function ${name} not found`);
    pieces.push(extractStatement(pageCode, start));
  }
  const context = vm.createContext({});
  vm.runInContext(pieces.join('\n'), context);
  return context;
}

function suggestFixture() {
  const page = pageLogic();
  const bases = ['#5A3CB0', '#FF0000', '#00FF00', '#0000FF', '#808080', '#FFFFFF', '#000000', '#3675AB', '#F7A47E', '#12FF34',
    '#FFD291', '#211D38', '#FEFEFE', '#010101', '#7F8081', '#C0FFEE', '#BADA55', '#FF00FF', '#00FFFF', '#FFFF00', '#804020'];
  const out = { hsl: [], fromHsl: [], wheel: [], suggest: [], themes: page.themes, wheelStyles: page.wheelStyles,
    aestheticStyles: page.aestheticStyles, brandRoles: page.brandRoles };
  for (const base of bases) {
    const { hue, saturation, lightness } = page.toHsl(base);
    out.hsl.push({ color: base, hue, saturation, lightness });
    for (const steps of [-1, 1, 2, 4, 5, 6, 7, 8]) out.wheel.push({ hue, steps, result: page.shiftOnWheel(hue, steps) });
  }
  for (const hue of [0, 15, 29.999, 60, 137.508, 220, 359.9, 360, 725.5, -30]) {
    for (const saturation of [0, .08, .43, .91, 1]) {
      for (const lightness of [0, .06, .34, .5, .68, .96, 1]) out.fromHsl.push({ hue, saturation, lightness, color: page.fromHsl(hue, saturation, lightness) });
    }
  }
  const modes = ['monochromatic', 'analogous', 'complementary', 'split', 'triadic', 'tetradic', 'neutral', 'pastel', 'vibrant', 'seasonal'];
  for (const base of bases) {
    for (const mode of modes) {
      for (const size of [3, 5, 6, 12]) {
        for (const theme of mode === 'seasonal' ? Object.keys(page.themes) : ['Spring']) {
          out.suggest.push({ mode, base, size, theme, colors: Array.from(page.suggestColors(mode, base, size, theme)) });
        }
      }
    }
  }
  return out;
}

fs.mkdirSync(outDir, { recursive: true });
fs.writeFileSync(path.join(outDir, '_suggest.json'), JSON.stringify(suggestFixture()));
console.log('_suggest.json');
for (const file of fs.readdirSync(imageDir).filter(file => file.endsWith('.png')).sort()) {
  const name = path.basename(file, '.png');
  const fixture = await fixtureFor(name);
  fs.writeFileSync(path.join(outDir, `${name}.json`), JSON.stringify(fixture));
  console.log(`${name}.json`, fixture.error ? `error: ${fixture.error}` : `${fixture.ready.distinct} colors`);
}
