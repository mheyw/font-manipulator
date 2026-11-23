"""Long-lived engine for the server: persistent worker pool + glyph result cache."""
from __future__ import annotations

import hashlib
import os
import threading
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor

from .fontio import load_font
from .pipeline import GlyphResult, process_glyph
from .recipe import Recipe

_FONTS: "OrderedDict[str, object]" = OrderedDict()


def _worker(font_key: str, font_bytes: bytes | None, recipe_json: str, names: list[str]):
    f = _FONTS.get(font_key)
    if f is None:
        if font_bytes is None:
            return None  # caller resends with bytes
        f = load_font(font_bytes)
        _FONTS[font_key] = f
        while len(_FONTS) > 4:
            _FONTS.popitem(last=False)
    recipe = Recipe.model_validate_json(recipe_json)
    return [process_glyph(f, recipe, n) for n in names]


class Engine:
    def __init__(self, jobs: int | None = None, cache_size: int = 20000):
        self.jobs = jobs or max(1, (os.cpu_count() or 2) - 1)
        self.pool = ProcessPoolExecutor(self.jobs)
        self.cache: "OrderedDict[tuple, GlyphResult]" = OrderedDict()
        self.cache_size = cache_size
        self.lock = threading.Lock()

    @staticmethod
    def recipe_key(recipe: Recipe) -> str:
        # output names don't affect geometry
        d = recipe.model_dump(exclude={"output", "name"})
        return hashlib.sha1(repr(d).encode()).hexdigest()

    def run(self, font_key: str, font_bytes: bytes, recipe: Recipe, names: list[str],
            progress=None) -> dict[str, GlyphResult]:
        rk = self.recipe_key(recipe)
        out, todo = {}, []
        with self.lock:
            for n in names:
                r = self.cache.get((font_key, rk, n))
                if r is not None:
                    out[n] = r
                    self.cache.move_to_end((font_key, rk, n))
                else:
                    todo.append(n)
        if todo:
            rj = recipe.model_dump_json()
            chunk = max(2, min(32, len(todo) // (self.jobs * 3) or 2))
            batches = [todo[i:i + chunk] for i in range(0, len(todo), chunk)]
            futs = [self.pool.submit(_worker, font_key, font_bytes, rj, b) for b in batches]
            done = len(out)
            for f in futs:
                for r in f.result():
                    out[r.name] = r
                    with self.lock:
                        self.cache[(font_key, rk, r.name)] = r
                        if len(self.cache) > self.cache_size:
                            self.cache.popitem(last=False)
                done += len(f.result())
                if progress:
                    progress(done, len(names))
        return {n: out[n] for n in names}

    def shutdown(self):
        self.pool.shutdown(cancel_futures=True)
