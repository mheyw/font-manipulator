"""fontmanip command line."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .api import make_font, to_bytes
from .effects import effects_schema
from .fontio import load_font
from .recipe import DEFAULT_RECIPE, Recipe


def _progress(done, total):
    w = 30
    f = int(w * done / max(total, 1))
    sys.stderr.write(f"\r  [{'#' * f}{'.' * (w - f)}] {done}/{total}")
    if done == total:
        sys.stderr.write("\n")


def cmd_build(a):
    recipe = Recipe.load(a.recipe) if a.recipe else DEFAULT_RECIPE
    if a.family:
        recipe.output.family = a.family
    loc = dict(kv.split("=") for kv in a.location) if a.location else None
    font = load_font(a.font, {k: float(v) for k, v in (loc or {}).items()})
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    print(f"{recipe.name}: {len(recipe.stops)} stops on {a.font}")
    res = make_font(font, recipe, text=a.text, jobs=a.jobs, progress=_progress)
    stem = recipe.output.family.replace(" ", "")
    (out / f"{stem}-VF.ttf").write_bytes(to_bytes(res.font))
    (out / f"{stem}-VF.woff2").write_bytes(to_bytes(res.font, "woff2"))
    recipe.dump(out / "recipe.json")
    (out / "report.json").write_text(json.dumps(res.report, indent=2))
    r = res.report
    print(f"  {r['glyph_count']} glyphs in {r['seconds']}s; varying {r['glyphs_in_gvar']}/{r['glyphs_changed']} changed")
    print(f"  stop fit (mean): {r['fit_mean']}  (min): {r['fit_min']}")
    print(f"  wrote {out}/{stem}-VF.ttf ({(out / f'{stem}-VF.ttf').stat().st_size // 1024} KB), "
          f"woff2 ({(out / f'{stem}-VF.woff2').stat().st_size // 1024} KB)")
    if r["missing"]:
        print(f"  WARNING: {len(r['missing'])} changed glyphs do not vary: {r['missing'][:10]}")
        return 1
    return 0


def cmd_effects(a):
    print(json.dumps(effects_schema(), indent=2))


def cmd_recipe(a):
    print(DEFAULT_RECIPE.model_dump_json(indent=2))


def main(argv=None):
    p = argparse.ArgumentParser(prog="fontmanip")
    sp = p.add_subparsers(dest="cmd", required=True)
    b = sp.add_parser("build", help="build a variable font from a font + recipe")
    b.add_argument("font")
    b.add_argument("recipe", nargs="?")
    b.add_argument("-o", "--out", default="out")
    b.add_argument("--text", help="only process glyphs needed for this text (fast preview)")
    b.add_argument("--family", help="output family name")
    b.add_argument("--jobs", type=int)
    b.add_argument("--location", nargs="*", help="pin variable input, e.g. wght=700 opsz=14")
    b.set_defaults(fn=cmd_build)
    sp.add_parser("effects", help="print effect schema (JSON)").set_defaults(fn=cmd_effects)
    sp.add_parser("recipe", help="print the default recipe (JSON)").set_defaults(fn=cmd_recipe)
    a = p.parse_args(argv)
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
