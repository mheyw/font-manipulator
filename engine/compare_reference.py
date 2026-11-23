# Usage: uv run python compare_reference.py RECIPE FONT "axis=val,..." "TEXT" REF_IMAGE OUT.png FONT_PX
"""Build a recipe on some text and render each stop next to a reference image."""
import base64, json, subprocess, sys
from pathlib import Path
from fontmanip.api import make_font, to_bytes
from fontmanip.fontio import load_font
from fontmanip.recipe import Recipe

recipe_path, font_path, loc, text, ref, out, size = sys.argv[1:8]
location = {k: float(v) for k, v in (kv.split("=") for kv in loc.split(",") if kv)} or None
r = Recipe.load(recipe_path)
font = load_font(font_path, location)
res = make_font(font, r, text=text, jobs=4)
woff = base64.b64encode(to_bytes(res.font, "woff2")).decode()
src = base64.b64encode(to_bytes(font, "woff2")).decode()
refb = base64.b64encode(Path(ref).read_bytes()).decode()
rows = "".join(
    f'<div class="row" style="font-variation-settings:\'{r.axis.tag}\' {s.position}">{text}<small>{s.position} · {s.name}</small></div>'
    for s in r.stops)
html = f"""<html><head><meta charset="utf-8"><style>
@font-face{{font-family:D;src:url(data:font/woff2;base64,{woff})}}
body{{margin:0;display:flex;background:#f3f1ec}} .ref{{width:46%;padding:14px}} .ref img{{width:100%}}
.out{{flex:1;padding:14px 20px;font-family:D;font-size:{size}px;line-height:1.25;background:#fff}}
.row{{white-space:nowrap;margin-bottom:10px}} small{{display:block;font:12px monospace;color:#c00}}
</style></head><body><div class="ref"><img src="data:image/jpeg;base64,{refb}"></div><div class="out">{rows}</div></body></html>"""
Path(out).with_suffix(".html").write_text(html)
subprocess.run(["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "--headless=new", "--disable-gpu",
                "--hide-scrollbars", f"--screenshot={out}", "--window-size=1500,760",
                "file://" + str(Path(out).with_suffix(".html"))], capture_output=True)
print(json.dumps({k: res.report[k] for k in ("fit_mean", "morph_mean", "seconds", "missing")}))
