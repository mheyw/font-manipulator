import { loadPyodide } from "/tmp/fontlab/pyo/node_modules/pyodide/pyodide.mjs";
import fs from "node:fs";
const py = await loadPyodide();
await py.loadPackage(["fonttools", "micropip", "pyclipper"]);
await py.pyimport("micropip").install("booleanOperations");
py.FS.writeFile("/in.otf", fs.readFileSync("/tmp/fontlab/data/SourceSans3-Regular.otf"));
py.FS.writeFile("/bool_test.py", fs.readFileSync("/tmp/fontlab/scripts/bool_test.py"));
py.runPython(`import sys; sys.argv=['x','/in.otf']; exec(open('/bool_test.py').read())`);
