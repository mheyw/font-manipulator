"""Local HTTP API for the React UI.  Run: `fontmanip-serve` (localhost only)."""
from __future__ import annotations

import base64
import copy
import hashlib
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .api import glyph_svg_paths, subset_to_text
from .build import BuildError, build_variable_font, to_bytes, varying_glyph_report
from .effects import effects_schema
from .engine import Engine
from .fontio import font_info, font_to_bytes, load_font
from .recipe import DEFAULT_RECIPE, Recipe

ROOT = Path(__file__).resolve().parents[3]
BUNDLED = ROOT / "fonts"
WEB_DIST = ROOT / "web" / "dist"


class FontEntry:
    def __init__(self, fid: str, label: str, font, origin: str, axes: list[dict], raw: bytes,
                 location: dict | None):
        self.id, self.label, self.font, self.origin, self.axes = fid, label, font, origin, axes
        self.raw, self.location = raw, location or {}
        self.bytes = font_to_bytes(font)
        self.info = font_info(font)


FONTS: dict[str, FontEntry] = {}
JOBS: dict[str, dict] = {}
ENGINE: Engine | None = None


def _axes(raw: bytes) -> list[dict]:
    from io import BytesIO

    from fontTools.ttLib import TTFont
    f = TTFont(BytesIO(raw), lazy=True)
    if "fvar" not in f:
        return []
    return [{"tag": a.axisTag, "min": a.minValue, "default": a.defaultValue, "max": a.maxValue}
            for a in f["fvar"].axes]


def register_font(raw: bytes, label: str, origin: str, location: dict | None = None) -> FontEntry:
    fid = hashlib.sha1(raw + repr(sorted((location or {}).items())).encode()).hexdigest()[:12]
    if fid not in FONTS:
        axes = _axes(raw)
        loc = {a["tag"]: a["default"] for a in axes} | (location or {})
        FONTS[fid] = FontEntry(fid, label, load_font(raw, location), origin, axes, raw, loc if axes else {})
    return FONTS[fid]


@asynccontextmanager
async def lifespan(app):
    global ENGINE
    ENGINE = Engine()
    for p in sorted(BUNDLED.glob("*")):
        if p.suffix.lower() in (".otf", ".ttf", ".woff", ".woff2"):
            register_font(p.read_bytes(), p.stem, "bundled")
    yield
    ENGINE.shutdown()


app = FastAPI(title="font-manipulator", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


def _font(fid: str) -> FontEntry:
    if fid not in FONTS:
        raise HTTPException(404, "unknown font")
    return FONTS[fid]


def _describe(e: FontEntry) -> dict:
    return {"id": e.id, "label": e.label, "origin": e.origin, "family": e.info.family,
            "glyphs": len(e.font.getGlyphOrder()), "upm": e.info.upm, "axes": e.axes,
            "location": e.location,
            "metrics": {"ascender": e.info.ascender, "descender": e.info.descender,
                        "xHeight": e.info.x_height, "capHeight": e.info.cap_height}}


@app.get("/api/effects")
def get_effects():
    return effects_schema()


@app.get("/api/recipe/default")
def get_default_recipe():
    return DEFAULT_RECIPE.model_dump()


RECIPES_DIR = ROOT / "engine" / "recipes"


@app.get("/api/recipes")
def list_recipes():
    out = [{"id": "default", "name": DEFAULT_RECIPE.name, "recipe": DEFAULT_RECIPE.model_dump()}]
    for p in sorted(RECIPES_DIR.glob("*.json")):
        try:
            r = Recipe.load(p)
            out.append({"id": p.stem, "name": r.name, "recipe": r.model_dump()})
        except Exception:  # noqa: BLE001  (skip invalid preset files)
            continue
    return out


@app.get("/api/fonts")
def list_fonts():
    return [_describe(e) for e in FONTS.values()]


@app.post("/api/fonts")
async def upload_font(file: UploadFile = File(...), location: str = Form("")):
    raw = await file.read()
    loc = {}
    for kv in filter(None, location.split(",")):
        k, v = kv.split("=")
        loc[k.strip()] = float(v)
    try:
        e = register_font(raw, Path(file.filename or "upload").stem, "upload", loc or None)
    except Exception as ex:  # noqa: BLE001
        raise HTTPException(400, f"could not read font: {ex}")
    return _describe(e)


class PinReq(BaseModel):
    location: dict[str, float]


@app.post("/api/fonts/{fid}/pin")
def pin_font(fid: str, req: PinReq):
    """Re-instance a variable source at a new axis location (returns a new font entry)."""
    e = _font(fid)
    if not e.axes:
        raise HTTPException(400, "not a variable font")
    loc = ", ".join(f"{k} {v:g}" for k, v in sorted(req.location.items()))
    return _describe(register_font(e.raw, f"{e.label.split(' @ ')[0]} @ {loc}", e.origin, req.location))


@app.get("/api/fonts/{fid}/source.woff2")
def source_font(fid: str):
    e = _font(fid)
    f = copy.deepcopy(e.font)
    return Response(to_bytes(f, "woff2"), media_type="font/woff2")


class PreviewReq(BaseModel):
    font_id: str
    recipe: Recipe
    text: str


@app.post("/api/preview")
def preview(req: PreviewReq):
    e = _font(req.font_id)
    text = req.text or "a"
    t = time.time()
    sub = subset_to_text(e.font, text)
    names = sub.getGlyphOrder()
    results = ENGINE.run(e.id, e.bytes, req.recipe, names)
    try:
        vf = build_variable_font(sub, req.recipe, results)
    except BuildError as ex:
        raise HTTPException(500, str(ex))
    rep = varying_glyph_report(vf, results)
    fits = [r.fit for r in results.values() if any(len(m) for m in r.masters)]
    return {"font": base64.b64encode(to_bytes(vf, "woff2")).decode(), "seconds": round(time.time() - t, 2),
            "glyphs": len(names), "missing": rep["missing"],
            "fit": [round(min(f[i] for f in fits), 3) for i in range(len(req.recipe.stops))] if fits else []}


class GlyphReq(BaseModel):
    font_id: str
    recipe: Recipe
    char: str


@app.post("/api/glyph")
def glyph(req: GlyphReq):
    e = _font(req.font_id)
    name = e.font.getBestCmap().get(ord(req.char[0])) if req.char else None
    if not name:
        raise HTTPException(404, "glyph not in font")
    r = ENGINE.run(e.id, e.bytes, req.recipe, [name])[name]
    return {"glyph": name, "advance": e.font["hmtx"][name][0], "paths": glyph_svg_paths(r),
            "points": [sum(len(c) for c in m) for m in r.masters], "fit": r.fit}


class ExportReq(BaseModel):
    font_id: str
    recipe: Recipe


def _export(job_id: str, e: FontEntry, recipe: Recipe):
    job = JOBS[job_id]
    try:
        def prog(done, total):
            job.update(done=done, total=total)
        names = e.font.getGlyphOrder()
        results = ENGINE.run(e.id, e.bytes, recipe, names, progress=prog)
        job["stage"] = "building"
        vf = build_variable_font(e.font, recipe, results)
        job["report"] = varying_glyph_report(vf, results)
        job["files"] = {"ttf": to_bytes(vf), "woff2": to_bytes(vf, "woff2"),
                        "recipe": recipe.model_dump_json(indent=2).encode()}
        job["sizes"] = {k: len(v) for k, v in job["files"].items()}
        job["stage"] = "done"
    except Exception as ex:  # noqa: BLE001
        job.update(stage="error", error=str(ex))


@app.post("/api/export")
def export(req: ExportReq):
    e = _font(req.font_id)
    jid = uuid.uuid4().hex[:10]
    JOBS[jid] = {"stage": "processing", "done": 0, "total": len(e.font.getGlyphOrder()),
                 "family": req.recipe.output.family, "started": time.time()}
    threading.Thread(target=_export, args=(jid, e, req.recipe), daemon=True).start()
    return {"job": jid}


@app.get("/api/export/{jid}")
def export_status(jid: str):
    j = JOBS.get(jid) or {}
    if not j:
        raise HTTPException(404)
    return {k: v for k, v in j.items() if k != "files"} | {"elapsed": round(time.time() - j["started"], 1)}


@app.get("/api/export/{jid}/{kind}")
def export_file(jid: str, kind: str):
    j = JOBS.get(jid)
    if not j or j.get("stage") != "done" or kind not in j["files"]:
        raise HTTPException(404)
    stem = j["family"].replace(" ", "")
    ext = {"ttf": "-VF.ttf", "woff2": "-VF.woff2", "recipe": "-recipe.json"}[kind]
    mt = {"ttf": "font/ttf", "woff2": "font/woff2", "recipe": "application/json"}[kind]
    return Response(j["files"][kind], media_type=mt,
                    headers={"Content-Disposition": f'attachment; filename="{stem}{ext}"'})


if WEB_DIST.exists():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")


def main():
    import uvicorn
    uvicorn.run("fontmanip.server:app", host="127.0.0.1", port=8765, reload=False)
