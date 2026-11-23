// opentype.js round trip: parse OTF, grid-snap each glyph path, write OTF, encode WOFF2
import fs from "node:fs";
import { createRequire } from "node:module";
const require = createRequire("/tmp/fontlab/js/package.json");
const opentype = await import("/tmp/fontlab/js/node_modules/opentype.js/dist/opentype.mjs");
const src = process.argv[2], out = process.argv[3];
const buf = fs.readFileSync(src);
const font = opentype.parse(buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
console.log("tables in source:", Object.keys(font.tables).join(","));
const G = 50, s = v => Math.round(v / G) * G;
for (let i = 0; i < font.glyphs.length; i++) {
  const g = font.glyphs.get(i);
  const p = g.path; // getter parses lazily
  for (const c of p.commands) {
    for (const k of ["x", "y", "x1", "y1", "x2", "y2"]) if (k in c) c[k] = s(c[k]);
  }
  g.path = p;
}
const ab = font.toArrayBuffer();
fs.writeFileSync(out, Buffer.from(ab));
console.log("wrote", out, ab.byteLength);
try {
  const { compress } = await import("/tmp/fontlab/js/node_modules/woff2-encoder/dist/index.js").catch(() => require("woff2-encoder"));
  const w = await compress(new Uint8Array(ab));
  fs.writeFileSync(out.replace(/\.otf$/, ".woff2"), w);
  console.log("woff2", w.byteLength);
} catch (e) { console.log("woff2-encoder failed:", e.message); }
