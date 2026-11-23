export type ParamSchema = {
  name: string
  label: string
  kind: 'float' | 'int' | 'bool' | 'choice'
  default: number | string | boolean
  min?: number
  max?: number
  step?: number
  options?: string[]
  help?: string
}

export type EffectSchema = {
  name: string
  label: string
  group: string
  help: string
  params: ParamSchema[]
}

export type EffectSpec = { type: string; enabled?: boolean; [param: string]: unknown }

export type Stop = {
  position: number
  name: string
  base: 'source' | 'previous'
  effects: EffectSpec[]
  smooth: number
}

export type Recipe = {
  name: string
  seed: number
  axis: { tag: string; name: string; default?: number | null }
  quality: { resolution: number; step: number; outline: 'lines' | 'smooth'; margin: number; min_area: number }
  output: { family: string }
  base?: { width: number; height: number; slant: number; tracking: number }
  stops: Stop[]
}

export type FontDesc = {
  id: string
  label: string
  origin: 'bundled' | 'upload'
  family: string
  glyphs: number
  upm: number
  axes: { tag: string; min: number; default: number; max: number }[]
  location: Record<string, number>
  metrics: { ascender: number; descender: number; xHeight: number; capHeight: number }
}

export type PreviewResult = { font: string; seconds: number; glyphs: number; missing: string[]; fit: number[] }
export type GlyphResult = { glyph: string; advance: number; paths: string[]; points: number[]; fit: number[] }
export type ExportStatus = {
  stage: 'processing' | 'building' | 'done' | 'error'
  done: number
  total: number
  elapsed: number
  error?: string
  sizes?: Record<string, number>
  report?: { missing: string[] }
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status}`
    try {
      const b = await r.json()
      msg = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail)
    } catch { /* not json */ }
    throw new Error(msg)
  }
  return r.json()
}

const post = (url: string, body: unknown, signal?: AbortSignal) =>
  fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal })

export const api = {
  effects: () => fetch('/api/effects').then(j<EffectSchema[]>),
  defaultRecipe: () => fetch('/api/recipe/default').then(j<Recipe>),
  presets: () => fetch('/api/recipes').then(j<{ id: string; name: string; recipe: Recipe }[]>),
  fonts: () => fetch('/api/fonts').then(j<FontDesc[]>),
  upload: (file: File, location = '') => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('location', location)
    return fetch('/api/fonts', { method: 'POST', body: fd }).then(j<FontDesc>)
  },
  pin: (id: string, location: Record<string, number>) =>
    post(`/api/fonts/${id}/pin`, { location }).then(j<FontDesc>),
  preview: (font_id: string, recipe: Recipe, text: string, signal?: AbortSignal) =>
    post('/api/preview', { font_id, recipe, text }, signal).then(j<PreviewResult>),
  glyph: (font_id: string, recipe: Recipe, char: string, signal?: AbortSignal) =>
    post('/api/glyph', { font_id, recipe, char }, signal).then(j<GlyphResult>),
  exportStart: (font_id: string, recipe: Recipe) => post('/api/export', { font_id, recipe }).then(j<{ job: string }>),
  exportStatus: (job: string) => fetch(`/api/export/${job}`).then(j<ExportStatus>),
  exportUrl: (job: string, kind: 'ttf' | 'woff2' | 'recipe') => `/api/export/${job}/${kind}`,
}

export function b64ToBuffer(b64: string): ArrayBuffer {
  const bin = atob(b64)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out.buffer
}

export function defaultsFor(e: EffectSchema): EffectSpec {
  const spec: EffectSpec = { type: e.name }
  for (const p of e.params) spec[p.name] = p.default
  return spec
}
