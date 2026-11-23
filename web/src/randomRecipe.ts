import type { EffectSchema, EffectSpec, Recipe, Stop } from './api'

const r = (a: number, b: number) => a + Math.random() * (b - a)
const ri = (a: number, b: number) => Math.round(r(a, b))
const pick = <T,>(xs: readonly T[]): T => xs[Math.floor(Math.random() * xs.length)]
const chance = (p: number) => Math.random() < p
const sign = () => (chance(0.5) ? -1 : 1)
const round = (v: number, step = 0.01) => Math.round(v / step) * step

type Look = Record<string, number | string>
/** t = intensity 0..1 (grows along the axis). */
type Gen = (t: number, look: Look) => Record<string, unknown>
type Def = { look?: () => Look; gen: Gen }

// ---------------------------------------------------------------- per-effect generators
const DEFS: Record<string, Def> = {
  wave: { look: () => ({ wl: ri(120, 420), axis: pick(['horizontal', 'vertical']), shape: pick(['noise', 'noise', 'sine']), amp: r(0.6, 1.4) }),
    gen: (t, l) => ({ amplitude: round((6 + 40 * t) * (l.amp as number), 1), wavelength: l.wl, axis: l.axis, shape: l.shape }) },
  jitter: { look: () => ({ scale: ri(40, 260) }), gen: (t, l) => ({ amount: round(4 + 38 * t, 1), scale: l.scale }) },
  skew: { look: () => ({ dir: sign() }), gen: (t, l) => ({ angle: round((l.dir as number) * (3 + 14 * t), 0.5), sx: 1, sy: 1 }) },
  smear: { look: () => ({ angle: pick([0, 0, 0, 90, ri(-35, 35)]), dir: pick(['forward', 'backward', 'both']), streak: ri(5, 20) }),
    gen: (t, l) => ({ length: round(15 + 130 * t, 1), angle: l.angle, direction: l.dir, variation: round(r(0.5, 1)),
      streak_size: l.streak, softness: round(r(1, 6), 0.5), level: round(r(0.36, 0.5)) }) },
  pixelate: { look: () => ({ cov: r(0.42, 0.56) }),
    gen: (t, l) => ({ columns: Math.max(3, Math.round(18 - 14 * t)), rows: Math.max(1, Math.round(7 - 5 * t)), coverage: round(l.cov as number), width_mode: 'advance' }) },
  magnet: { look: () => ({ dedupe: pick(['global', 'consecutive']) }),
    gen: (t, l) => ({ columns: Math.max(3, Math.round(17 - 12 * t)), rows: Math.max(1, Math.round(7 - 5 * t)), dedupe: l.dedupe }) },
  grow: { look: () => ({ dir: pick([1, 1, -1]) }), gen: (t, l) => ({ distance: round((l.dir as number) * (4 + 28 * t), 1) }) },
  inflate: { gen: (t) => ({ distance: round(8 + 45 * t, 1) }) },          // -> grow
  erode: { gen: (t) => ({ distance: round(-(4 + 16 * t), 1) }) },         // -> grow
  round: { gen: (t) => ({ radius: round(12 + 70 * t, 1), mode: 'both' }) },
  ink: { look: () => ({ scale: ri(20, 200), level: r(0.3, 0.5) }),
    gen: (t, l) => ({ radius: round(5 + 45 * t, 1), level: round(l.level as number), noise: round(0.04 + 0.26 * t), scale: l.scale }) },
  starve: { look: () => ({ scale: ri(10, 40) }),                          // -> ink, eroded/broken
    gen: (t, l) => ({ radius: round(4 + 8 * t, 1), level: round(0.5 + 0.08 * t), noise: round(0.15 + 0.3 * t), scale: l.scale }) },
  melt: { look: () => ({ drip: ri(20, 80) }),
    gen: (t, l) => ({ length: round(30 + 260 * t, 5), variation: round(r(0.6, 1)), drip_size: l.drip,
      swell: round(8 + 30 * t, 1), softness: round(8 + 16 * t, 0.5), level: round(r(0.33, 0.42)) }) },
  echo: { look: () => ({ dx: sign() * ri(6, 22), dy: sign() * ri(0, 12) }),
    gen: (t, l) => ({ dx: Math.round((l.dx as number) * (0.4 + t)), dy: Math.round((l.dy as number) * (0.4 + t)), copies: t > 0.6 ? 2 : 1 }) },
  grain: { look: () => ({ scale: ri(8, 40) }), gen: (t, l) => ({ amount: round(0.08 + 0.45 * t), scale: l.scale, octaves: 2 }) },
  spatter: { look: () => ({ size: ri(3, 10) }), gen: (t, l) => ({ density: round(0.05 + 0.3 * t), size: l.size, reach: round(20 + 90 * t, 5) }) },
  roughen: { look: () => ({ scale: ri(3, 20), bias: r(-0.3, 0.3) }),
    gen: (t, l) => ({ amount: round(1.5 + 10 * t, 0.5), scale: l.scale, octaves: ri(2, 3), bias: round(l.bias as number, 0.05) }) },
  pits: { look: () => ({ size: ri(6, 14) }), gen: (t, l) => ({ density: round(0.06 + 0.16 * t), size: l.size, margin: 8 }) },
}
const ALIAS: Record<string, string> = { inflate: 'grow', erode: 'grow', starve: 'ink' }
const REGIONAL = new Set(['ink', 'grow', 'roughen', 'smear', 'melt', 'echo'])

/** A layer = one effect in the chain. onset: only present once intensity t >= onset. */
type Layer = { kind: string; onset?: number; local?: boolean }

// ---------------------------------------------------------------- recipe archetypes (modelled on the presets)
type Arche = { name: string; layers: () => Layer[]; base?: () => Recipe['base']; localised?: boolean; crisp?: boolean }
const ARCHETYPES: Arche[] = [
  { name: 'motion', base: () => ({ width: round(r(1.3, 1.7)), height: round(r(0.55, 0.8)), slant: round(r(12, 24), 0.5), tracking: ri(-20, 20) }),
    layers: () => [{ kind: 'wave' }, ...(chance(0.6) ? [{ kind: 'wave', onset: 0.4 }] : []), { kind: 'smear' },
      { kind: 'ink' }, { kind: 'roughen' }] },
  { name: 'inflate', base: () => ({ width: round(r(1.1, 1.35)), height: 1, slant: 0, tracking: ri(10, 50) }),
    layers: () => [{ kind: 'inflate' }, { kind: 'round' }, { kind: 'ink' }, { kind: 'pits', onset: 0.2 }, { kind: 'roughen' }] },
  { name: 'melt', localised: true, layers: () => [{ kind: 'ink', local: true }, { kind: 'melt', local: true },
    ...(chance(0.4) ? [{ kind: 'grain', onset: 0.5 }] : [])] },
  { name: 'marker', localised: true, layers: () => [{ kind: 'echo', local: true, onset: 0.4 }, { kind: 'inflate', local: true },
    { kind: 'ink', local: true }, ...(chance(0.4) ? [{ kind: 'spatter', onset: 0.6 }] : [])] },
  { name: 'digital', crisp: true, layers: () => [...(chance(0.6) ? [{ kind: pick(['wave', 'jitter']) }] : []),
    { kind: pick(['pixelate', 'pixelate', 'magnet']) }, ...(chance(0.5) ? [{ kind: 'round', onset: 0.5 }] : []),
    ...(chance(0.5) ? [{ kind: 'ink', onset: 0.6 }] : []), { kind: 'roughen', onset: 0.3 }] },
  { name: 'erode', layers: () => [...(chance(0.5) ? [{ kind: 'jitter' }] : []), { kind: 'erode' }, { kind: 'starve' },
    { kind: 'roughen' }, ...(chance(0.6) ? [{ kind: 'spatter', onset: 0.3 }] : []), ...(chance(0.3) ? [{ kind: 'pits', onset: 0.5 }] : [])] },
]

const POOLS = {
  distort: ['wave', 'jitter', 'skew', 'smear', 'wave'],
  structure: ['pixelate', 'magnet', 'inflate', 'erode', 'round'],
  core: ['ink', 'ink', 'melt', 'inflate', 'starve'],
  texture: ['roughen', 'roughen', 'grain', 'spatter', 'pits', 'echo'],
}
const sample = (pool: string[], k: number) => {
  const p = [...pool]; const out: string[] = []
  while (out.length < k && p.length) { const x = p.splice(Math.floor(Math.random() * p.length), 1)[0]; if (!out.includes(x)) out.push(x) }
  return out
}

function freeLayers(): Layer[] {
  const L: Layer[] = []
  for (const k of sample(POOLS.distort, ri(0, 2))) L.push({ kind: k, onset: chance(0.3) ? 0.4 : 0 })
  if (chance(0.45)) L.push({ kind: pick(POOLS.structure) })
  for (const k of sample(POOLS.core, ri(1, 2))) L.push({ kind: k })
  for (const k of sample(POOLS.texture, ri(1, 3))) L.push({ kind: k, onset: chance(0.4) ? r(0.2, 0.6) : 0 })
  // avoid conflicting grids
  const grids = L.filter((l) => l.kind === 'pixelate' || l.kind === 'magnet')
  return grids.length > 1 ? L.filter((l) => l !== grids[1]) : L
}

/** A random, layered, progressively-decaying recipe built from the effects the engine offers. */
export function randomRecipe(effects: EffectSchema[], keep: Pick<Recipe, 'output' | 'quality' | 'axis'>): Recipe {
  const available = new Set(effects.map((e) => e.name))
  const arche = chance(0.5) ? pick(ARCHETYPES) : null
  let layers = arche ? arche.layers() : freeLayers()
  if (arche && chance(0.5)) {                                       // archetype + one extra layer for variety
    const extra = pick([...POOLS.distort, ...POOLS.texture])
    if (!layers.some((l) => l.kind === extra)) layers.push({ kind: extra, onset: r(0, 0.5) })
  }
  layers = layers.filter((l) => available.has(ALIAS[l.kind] ?? l.kind))
  const ORDER = ['wave', 'jitter', 'skew', 'smear', 'pixelate', 'magnet', 'erode', 'inflate', 'grow', 'round', 'ink', 'starve',
    'melt', 'echo', 'grain', 'spatter', 'pits', 'roughen']
  layers.sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind))
  const looks = layers.map((l) => DEFS[l.kind].look?.() ?? {})
  const localised = arche?.localised ?? chance(0.3)
  const regionSize = ri(200, 450)
  const crisp = arche?.crisp ?? layers.some((l) => l.kind === 'pixelate' || l.kind === 'magnet')

  const n = ri(3, 5)
  const stops: Stop[] = []
  for (let k = 0; k < n; k++) {
    const pos = Math.round((100 * k) / (n - 1))
    if (k === 0 && chance(0.7)) {
      stops.push({ position: 0, name: 'Clean', base: 'source', effects: [], smooth: 0.5 })
      continue
    }
    const t = Math.min(0.9, Math.max(0.05, 0.9 * (k / (n - 1)) * r(0.9, 1.1)))
    const fx: EffectSpec[] = []
    layers.forEach((l, i) => {
      if ((l.onset ?? 0) > t) return
      const spec: EffectSpec = { type: ALIAS[l.kind] ?? l.kind, ...DEFS[l.kind].gen(t, looks[i]) }
      if ((l.local || (localised && !arche)) && REGIONAL.has(spec.type)) {
        spec.regions = round(Math.min(0.9, 0.2 + 0.55 * t))
        spec.region_size = regionSize
      }
      fx.push(spec)
    })
    stops.push({ position: pos, name: ['Clean', 'Worn', 'Tired', 'Broken', 'Gone'][Math.min(4, Math.round(t * 4))] + (k ? ` ${k}` : ''),
      base: 'source', effects: fx, smooth: crisp ? 0 : 0.5 })
  }
  const base = arche?.base?.() ?? (chance(0.35)
    ? { width: round(r(0.85, 1.5)), height: round(r(0.8, 1.1)), slant: round(pick([0, 0, r(-6, 18)]), 0.5), tracking: ri(0, 40) }
    : { width: 1, height: 1, slant: 0, tracking: 0 })
  const label = arche ? arche.name : layers.map((l) => l.kind).join(' + ')
  return {
    name: `Random · ${label}`,
    seed: Math.floor(Math.random() * 1e6),
    axis: { ...keep.axis, default: 0 },
    quality: { ...keep.quality, outline: crisp && chance(0.5) ? 'lines' : keep.quality.outline },
    output: keep.output,
    base,
    stops,
  }
}
