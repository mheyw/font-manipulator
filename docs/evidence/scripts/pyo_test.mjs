// Test fontTools in Pyodide: load OTF, snap glyphs, booleanOperations union, save OTF+WOFF2
import { loadPyodide } from "/tmp/fontlab/pyo/node_modules/pyodide/pyodide.mjs";
import fs from "node:fs";

const t0 = Date.now();
const py = await loadPyodide();
await py.loadPackage(["fonttools", "brotli", "micropip", "pyclipper"]);
const micropip = py.pyimport("micropip");
let boolOk = true;
try { await micropip.install("booleanOperations"); } catch (e) { boolOk = false; console.log("booleanOperations install failed", String(e).slice(0, 300)); }
try { await micropip.install("skia-pathops"); console.log("skia-pathops installed?!"); } catch (e) { console.log("skia-pathops: not installable:", String(e).split("\n").slice(-2).join(" ").slice(0, 200)); }
console.log("load ms", Date.now() - t0);
py.FS.writeFile("/in.otf", fs.readFileSync(process.argv[2]));
const t1 = Date.now();
py.runPython(`
import fontTools
from fontTools.ttLib import TTFont
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
print("fontTools", fontTools.version)
f = TTFont("/in.otf")
cs = f["CFF "].cff.topDictIndex[0].CharStrings
gs = f.getGlyphSet()
G=50
s=lambda p:(round(p[0]/G)*G, round(p[1]/G)*G)
for n in f.getGlyphOrder():
    r=RecordingPen(); gs[n].draw(r)
    pen=T2CharStringPen(f["hmtx"][n][0], gs)
    for op,a in r.value:
        if op in ("closePath","endPath"): getattr(pen,op)()
        else: getattr(pen,op)(*[s(x) for x in a])
    cs[n]=pen.getCharString(private=cs[n].private, globalSubrs=cs[n].globalSubrs)
${boolOk ? `
import booleanOperations
from fontTools.pens.pointPen import SegmentToPointPen
print("booleanOperations", booleanOperations.__version__ if hasattr(booleanOperations,'__version__') else 'ok')
` : ""}
f.save("/out.otf")
f.flavor="woff2"; f.save("/out.woff2")
`);
console.log("process ms", Date.now() - t1);
fs.writeFileSync("/tmp/fontlab/data/pyo_out.otf", py.FS.readFile("/out.otf"));
fs.writeFileSync("/tmp/fontlab/data/pyo_out.woff2", py.FS.readFile("/out.woff2"));
console.log("done");
