import { useState } from 'react'
import { defaultsFor, type EffectSchema, type EffectSpec, type ParamSchema, type Recipe, type Stop } from './api'

type Props = {
  recipe: Recipe
  effects: EffectSchema[]
  onChange: (r: Recipe) => void
  activeStop: number
  onSelectStop: (i: number) => void
}

function ParamControl({ p, value, onChange }: { p: ParamSchema; value: unknown; onChange: (v: unknown) => void }) {
  if (p.kind === 'bool')
    return (
      <label className="param check" title={p.help}>
        <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
        {p.label}
      </label>
    )
  if (p.kind === 'choice')
    return (
      <label className="param" title={p.help}>
        <span>{p.label}</span>
        <select value={String(value)} onChange={(e) => onChange(e.target.value)}>
          {p.options!.map((o) => <option key={o}>{o}</option>)}
        </select>
      </label>
    )
  const num = Number(value)
  const parse = (s: string) => (p.kind === 'int' ? Math.round(Number(s)) : Number(s))
  return (
    <label className="param" title={p.help}>
      <span>{p.label}</span>
      <input type="range" min={p.min} max={p.max} step={p.step ?? (p.kind === 'int' ? 1 : 0.01)} value={num}
        onChange={(e) => onChange(parse(e.target.value))} />
      <input type="number" className="num" min={p.min} max={p.max} step={p.step ?? 'any'} value={num}
        onChange={(e) => onChange(parse(e.target.value))} />
    </label>
  )
}

function EffectCard({ spec, schema, onChange, onRemove, onMove }: {
  spec: EffectSpec
  schema?: EffectSchema
  onChange: (s: EffectSpec) => void
  onRemove: () => void
  onMove: (d: -1 | 1) => void
}) {
  const [open, setOpen] = useState(true)
  if (!schema) return <div className="effect error">Unknown effect {spec.type}</div>
  const enabled = spec.enabled !== false
  return (
    <div className={`effect ${enabled ? '' : 'disabled'}`}>
      <div className="effect-head">
        <button className="ghost" onClick={() => setOpen(!open)} title="collapse">{open ? '▾' : '▸'}</button>
        <input type="checkbox" checked={enabled} title="enable"
          onChange={(e) => onChange({ ...spec, enabled: e.target.checked })} />
        <strong title={schema.help}>{schema.label}</strong>
        <span className="tag">{schema.group}</span>
        <span className="spacer" />
        <button className="ghost" onClick={() => onMove(-1)} title="move up">↑</button>
        <button className="ghost" onClick={() => onMove(1)} title="move down">↓</button>
        <button className="ghost" onClick={onRemove} title="remove">✕</button>
      </div>
      {open && schema.params.map((p) => (
        <ParamControl key={p.name} p={p} value={spec[p.name] ?? p.default}
          onChange={(v) => onChange({ ...spec, [p.name]: v })} />
      ))}
    </div>
  )
}

export function RecipeEditor({ recipe, effects, onChange, activeStop, onSelectStop }: Props) {
  const byName = Object.fromEntries(effects.map((e) => [e.name, e]))
  const stop = recipe.stops[activeStop]
  const setStops = (stops: Stop[]) => onChange({ ...recipe, stops })
  const setStop = (i: number, s: Stop) => setStops(recipe.stops.map((x, k) => (k === i ? s : x)))
  const groups = [...new Set(effects.map((e) => e.group))]

  const addStop = () => {
    const last = recipe.stops[recipe.stops.length - 1]
    const s: Stop = { position: last.position + 25, name: `Stage ${recipe.stops.length}`, base: 'previous', effects: [], smooth: 0.5 }
    setStops([...recipe.stops, s])
    onSelectStop(recipe.stops.length)
  }
  const removeStop = (i: number) => {
    if (recipe.stops.length <= 2) return
    const stops = recipe.stops.filter((_, k) => k !== i)
    if (stops[0].base === 'previous') stops[0] = { ...stops[0], base: 'source' }
    setStops(stops)
    onSelectStop(Math.max(0, Math.min(activeStop, stops.length - 1)))
  }
  const moveEffect = (i: number, d: -1 | 1) => {
    const fx = [...stop.effects]
    const k = i + d
    if (k < 0 || k >= fx.length) return
    ;[fx[i], fx[k]] = [fx[k], fx[i]]
    setStop(activeStop, { ...stop, effects: fx })
  }

  return (
    <div className="recipe">
      <section>
        <h3>Axis</h3>
        <div className="row">
          <label className="param"><span>Tag</span>
            <input value={recipe.axis.tag} maxLength={4}
              onChange={(e) => onChange({ ...recipe, axis: { ...recipe.axis, tag: e.target.value.toUpperCase() } })} />
          </label>
          <label className="param"><span>Name</span>
            <input value={recipe.axis.name}
              onChange={(e) => onChange({ ...recipe, axis: { ...recipe.axis, name: e.target.value } })} />
          </label>
        </div>
      </section>

      <section>
        <h3>Stops <button className="small" onClick={addStop}>+ stop</button></h3>
        <div className="stops">
          {recipe.stops.map((s, i) => (
            <button key={i} className={`stop ${i === activeStop ? 'active' : ''}`} onClick={() => onSelectStop(i)}>
              <b>{s.position}</b> {s.name}
              <small>{s.effects.filter((e) => e.enabled !== false).length} fx</small>
            </button>
          ))}
        </div>
      </section>

      {stop && (
        <section className="stop-detail">
          <div className="row">
            <label className="param"><span>Name</span>
              <input value={stop.name} onChange={(e) => setStop(activeStop, { ...stop, name: e.target.value })} />
            </label>
            <label className="param"><span>Position</span>
              <input type="number" className="num" value={stop.position}
                onChange={(e) => setStop(activeStop, { ...stop, position: Number(e.target.value) })} />
            </label>
          </div>
          <div className="row">
            <label className="param"><span>Start from</span>
              <select value={stop.base} disabled={activeStop === 0}
                onChange={(e) => setStop(activeStop, { ...stop, base: e.target.value as Stop['base'] })}>
                <option value="source">original font</option>
                <option value="previous">previous stop</option>
              </select>
            </label>
            <label className="param" title="Outline smoothing after tracing. 0 keeps pixel edges crisp.">
              <span>Smooth</span>
              <input type="range" min={0} max={3} step={0.25} value={stop.smooth}
                onChange={(e) => setStop(activeStop, { ...stop, smooth: Number(e.target.value) })} />
              <span className="num">{stop.smooth}</span>
            </label>
            {activeStop > 0 && <button className="small danger" onClick={() => removeStop(activeStop)}>delete stop</button>}
          </div>

          {stop.effects.length === 0 && (
            <p className="hint">{activeStop === 0 && stop.base === 'source' ? 'Untouched original — the axis default.' : 'No effects yet.'}</p>
          )}
          {stop.effects.map((spec, i) => (
            <EffectCard key={i} spec={spec} schema={byName[spec.type]}
              onChange={(s) => setStop(activeStop, { ...stop, effects: stop.effects.map((x, k) => (k === i ? s : x)) })}
              onRemove={() => setStop(activeStop, { ...stop, effects: stop.effects.filter((_, k) => k !== i) })}
              onMove={(d) => moveEffect(i, d)} />
          ))}
          <select className="add-effect" value="" onChange={(e) => {
            const sch = byName[e.target.value]
            if (sch) setStop(activeStop, { ...stop, effects: [...stop.effects, defaultsFor(sch)] })
          }}>
            <option value="">+ add effect…</option>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {effects.filter((e) => e.group === g).map((e) => <option key={e.name} value={e.name}>{e.label}</option>)}
              </optgroup>
            ))}
          </select>
        </section>
      )}

      <section>
        <h3>Quality</h3>
        <label className="param" title="Raster pixels per em. Higher = finer detail, slower.">
          <span>Resolution</span>
          <input type="range" min={128} max={800} step={16} value={recipe.quality.resolution}
            onChange={(e) => onChange({ ...recipe, quality: { ...recipe.quality, resolution: Number(e.target.value) } })} />
          <span className="num">{recipe.quality.resolution}</span>
        </label>
        <label className="param" title="Point spacing on the output outline (em/1000). Bigger = smaller file.">
          <span>Point spacing</span>
          <input type="range" min={6} max={40} step={1} value={recipe.quality.step}
            onChange={(e) => onChange({ ...recipe, quality: { ...recipe.quality, step: Number(e.target.value) } })} />
          <span className="num">{recipe.quality.step}</span>
        </label>
        <label className="param"><span>Outline</span>
          <select value={recipe.quality.outline}
            onChange={(e) => onChange({ ...recipe, quality: { ...recipe.quality, outline: e.target.value as 'lines' | 'smooth' } })}>
            <option value="smooth">smooth curves</option>
            <option value="lines">crisp polygons</option>
          </select>
        </label>
        <label className="param"><span>Seed</span>
          <input type="number" className="num" value={recipe.seed}
            onChange={(e) => onChange({ ...recipe, seed: Math.round(Number(e.target.value)) })} />
          <button className="small" onClick={() => onChange({ ...recipe, seed: Math.floor(Math.random() * 1e6) })}>🎲</button>
        </label>
      </section>
    </div>
  )
}
