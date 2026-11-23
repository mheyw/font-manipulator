"""Recipe: the full description of a variation font. Serialisable to JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from .effects import REGISTRY


class Axis(BaseModel):
    tag: str = "DCAY"
    name: str = "Decay"
    default: float | None = Field(None, description="Axis default (must be a stop position); first stop if unset")

    @field_validator("tag")
    @classmethod
    def _tag(cls, v: str) -> str:
        if not re.fullmatch(r"[A-Z][A-Z0-9]{3}", v):
            raise ValueError("custom axis tags must be 4 chars, uppercase letter first, then A-Z/0-9")
        return v


class Stop(BaseModel):
    position: float
    name: str
    base: Literal["source", "previous"] = "source"
    effects: list[dict] = Field(default_factory=list)
    smooth: float = Field(0.5, ge=0, le=3, description="Outline smoothing after tracing (0 = crisp pixels)")

    @field_validator("effects")
    @classmethod
    def _effects(cls, v: list[dict]) -> list[dict]:
        for e in v:
            if e.get("type") not in REGISTRY:
                raise ValueError(f"unknown effect {e.get('type')!r}")
        return v


class Quality(BaseModel):
    resolution: int = Field(320, ge=64, le=2000, description="Raster pixels per em")
    step: float = Field(20.0, ge=2, le=80, description="Point spacing on output outlines (u)")
    outline: Literal["lines", "smooth"] = Field(
        "smooth", description="lines: polygon (crisp). smooth: all points off-curve = quadratic spline (fewer points, rounder)")
    margin: float = Field(150.0, ge=0, le=1000, description="Raster margin around each glyph (u)")
    min_area: float = Field(60.0, ge=0, description="Drop traced specks smaller than this (u²)")


class Base(BaseModel):
    """Applied to the source before any stop, including outlines AND advance widths."""
    width: float = Field(1.0, ge=0.2, le=4.0, description="Horizontal scale (advances scale too)")
    height: float = Field(1.0, ge=0.2, le=4.0, description="Vertical scale from the baseline")
    slant: float = Field(0.0, ge=-45, le=45, description="Oblique angle in degrees")
    tracking: float = Field(0.0, ge=-300, le=600, description="Extra advance per glyph (u)")

    def is_identity(self) -> bool:
        return self.width == 1 and self.height == 1 and self.slant == 0 and self.tracking == 0


class Output(BaseModel):
    family: str = "FontManip Decay"


class Recipe(BaseModel):
    name: str = "Untitled"
    seed: int = 1
    axis: Axis = Axis()
    quality: Quality = Quality()
    output: Output = Output()
    base: Base = Base()
    stops: list[Stop]

    @model_validator(mode="after")
    def _stops(self):
        if len(self.stops) < 2:
            raise ValueError("a recipe needs at least two stops")
        pos = [s.position for s in self.stops]
        if pos != sorted(pos) or len(set(pos)) != len(pos):
            raise ValueError("stop positions must be strictly increasing")
        if self.stops[0].base == "previous":
            raise ValueError("the first stop must be based on the source")
        if self.axis.default is not None and self.axis.default not in pos:
            raise ValueError("axis.default must equal one of the stop positions")
        return self

    def default_index(self) -> int:
        pos = [s.position for s in self.stops]
        return pos.index(self.axis.default) if self.axis.default is not None else 0

    @classmethod
    def load(cls, path: str | Path) -> "Recipe":
        return cls.model_validate(json.loads(Path(path).read_text()))

    def dump(self, path: str | Path) -> None:
        Path(path).write_text(self.model_dump_json(indent=2))


DEFAULT_RECIPE = Recipe(
    name="Ink decay",
    stops=[
        Stop(position=0, name="Fresh"),
        Stop(position=33, name="Bleed", effects=[
            {"type": "ink", "radius": 14, "level": 0.32, "noise": 0.18, "scale": 40}]),
        Stop(position=66, name="Pixel", smooth=0.0, effects=[
            {"type": "pixelate", "columns": 10, "rows": 4}]),
        Stop(position=100, name="Blob", base="previous", effects=[
            {"type": "ink", "radius": 40, "level": 0.5, "noise": 0.25, "scale": 90}]),
    ],
)
