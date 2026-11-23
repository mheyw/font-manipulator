import { useCallback, useEffect, useRef, useState } from 'react'
import { api, b64ToBuffer, type EffectSchema, type ExportStatus, type FontDesc, type GlyphResult, type Recipe } from './api'
import { RecipeEditor } from './RecipeEditor'
import { randomRecipe } from './randomRecipe'

const STORE = 'fontmanip.recipe.v1'

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

function Inspector({ glyph, recipe, axisValue, font }: { glyph: GlyphResult | null; recipe: Recipe; axisValue: number; font?: FontDesc }) {
  if (!glyph || !font) return <div className="inspector empty">Click a letter in the tester to inspect it.</div>
  const pad = font.upm * 0.1
  const top = Math.max(font.metrics.ascender, font.metrics.capHeight) + pad
  const bottom = Math.min(font.metrics.descender, 0) - pad
  const pos = recipe.stops.map((s) => s.position)
  const nearest = pos.reduce((b, p, i) => (Math.abs(p - axisValue) < Math.abs(pos[b] - axisValue) ? i : b), 0)
  return (
    <div className="inspector">
      <div className="inspector-head">
        <b>{glyph.glyph}</b>
        <span>{glyph.points[0]} points / master</span>
      </div>
      <div className="masters">
        {glyph.paths.map((d, i) => (
          <figure key={i} className={i === nearest ? 'near' : ''}>
            <svg viewBox={`${-pad} ${-top} ${glyph.advance + 2 * pad} ${top - bottom}`}>
              <line x1={-pad} x2={glyph.advance + pad} y1={0} y2={0} className="baseline"
                style={{ strokeWidth: font.upm / 250 }} />
              <g transform="scale(1,-1)">
                <path d={d} fillRule="nonzero" />
              </g>
            </svg>
            <figcaption>
              {recipe.stops[i]?.position} {recipe.stops[i]?.name}
              <small> fit {(glyph.fit[i] * 100).toFixed(1)}%</small>
            </figcaption>
          </figure>
        ))}
      </div>
    </div>
  )
}

export default function App() {
  const [effects, setEffects] = useState<EffectSchema[]>([])
  const [fonts, setFonts] = useState<FontDesc[]>([])
  const [fontId, setFontId] = useState<string>('')
  const [recipe, setRecipe] = useState<Recipe | null>(null)
  const [activeStop, setActiveStop] = useState(1)
  const [text, setText] = useState('Expired by the grid — eagsB8 Ferro')
  const [axisValue, setAxisValue] = useState(0)
  const [animate, setAnimate] = useState(false)
  const [size, setSize] = useState(120)
  const [previewFamily, setPreviewFamily] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [inspectChar, setInspectChar] = useState('g')
  const [glyph, setGlyph] = useState<GlyphResult | null>(null)
  const [job, setJob] = useState<string>('')
  const [jobStatus, setJobStatus] = useState<ExportStatus | null>(null)
  const [pin, setPin] = useState<Record<string, number>>({})
  const [presets, setPresets] = useState<{ id: string; name: string; recipe: Recipe }[]>([])
  const counter = useRef(0)
  const fontFaces = useRef<FontFace[]>([])
  const testerRef = useRef<HTMLDivElement>(null)
  const testerInit = useRef(false)

  useEffect(() => {
    api.presets().then(setPresets).catch(() => {})
    Promise.all([api.effects(), api.fonts(), api.defaultRecipe()]).then(([fx, fs, def]) => {
      setEffects(fx)
      setFonts(fs)
      setFontId(fs[0]?.id ?? '')
      const saved = localStorage.getItem(STORE)
      setRecipe(saved ? JSON.parse(saved) : def)
    }).catch((e) => setError(`Engine not reachable — is "fontmanip-serve" running? (${e.message})`))
  }, [])

  useEffect(() => { if (recipe) localStorage.setItem(STORE, JSON.stringify(recipe)) }, [recipe])

  const dRecipe = useDebounced(recipe, 350)
  const dText = useDebounced(text, 350)

  // live preview: compile a variable font for the tester text
  useEffect(() => {
    if (!dRecipe || !fontId) return
    const ctl = new AbortController()
    setBusy(true)
    api.preview(fontId, dRecipe, dText, ctl.signal).then(async (r) => {
      const fam = `preview-${++counter.current}`
      const face = new FontFace(fam, b64ToBuffer(r.font))
      await face.load()
      document.fonts.add(face)
      fontFaces.current.push(face)
      while (fontFaces.current.length > 3) document.fonts.delete(fontFaces.current.shift()!)
      setPreviewFamily(fam)
      setError(r.missing.length ? `${r.missing.length} glyph(s) failed to interpolate` : '')
      setStatus(`${r.glyphs} glyphs · ${r.seconds}s · worst stop fit ${r.fit.map((f) => (f * 100).toFixed(0) + '%').join(' / ')}`)
    }).catch((e) => { if (e.name !== 'AbortError') setError(e.message) })
      .finally(() => setBusy(false))
    return () => ctl.abort()
  }, [dRecipe, dText, fontId])

  // glyph inspector
  useEffect(() => {
    if (!dRecipe || !fontId || !inspectChar.trim()) return
    const ctl = new AbortController()
    api.glyph(fontId, dRecipe, inspectChar, ctl.signal).then(setGlyph).catch(() => {})
    return () => ctl.abort()
  }, [dRecipe, fontId, inspectChar])

  // animation
  useEffect(() => {
    if (!animate || !recipe) return
    const lo = recipe.stops[0].position, hi = recipe.stops[recipe.stops.length - 1].position
    let raf = 0
    const t0 = performance.now()
    const tick = (t: number) => {
      const ph = ((t - t0) / 4000) * Math.PI * 2
      setAxisValue(lo + (hi - lo) * (0.5 - 0.5 * Math.cos(ph)))
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [animate, recipe])

  // export polling
  useEffect(() => {
    if (!job) return
    const t = setInterval(async () => {
      const s = await api.exportStatus(job)
      setJobStatus(s)
      if (s.stage === 'done' || s.stage === 'error') clearInterval(t)
    }, 700)
    return () => clearInterval(t)
  }, [job])

  const onFiles = useCallback(async (files: FileList | null) => {
    const f = files?.[0]
    if (!f) return
    try {
      setStatus(`Loading ${f.name}…`)
      const d = await api.upload(f)
      setFonts((fs) => [...fs.filter((x) => x.id !== d.id), d])
      setFontId(d.id)
    } catch (e) {
      setError(`Upload failed: ${(e as Error).message}`)
    }
  }, [])

  const [svgBusy, setSvgBusy] = useState(false)
  const downloadSvg = async (allStops: boolean) => {
    if (!recipe) return
    setSvgBusy(true)
    try {
      const svg = await api.previewSvg(fontId, recipe, text, allStops ? null : [axisValue], allStops)
      const a = document.createElement('a')
      a.href = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }))
      const slug = (recipe.output.family || 'preview').replace(/\s+/g, '-')
      a.download = allStops ? `${slug}-all-stops.svg` : `${slug}-${recipe.axis.tag}${axisValue.toFixed(0)}.svg`
      a.click()
      setTimeout(() => URL.revokeObjectURL(a.href), 2000)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSvgBusy(false)
    }
  }

  const loadRecipeFile = async (files: FileList | null) => {
    const f = files?.[0]
    if (f) setRecipe(JSON.parse(await f.text()))
  }
  const saveRecipe = () => {
    if (!recipe) return
    const a = document.createElement('a')
    a.href = URL.createObjectURL(new Blob([JSON.stringify(recipe, null, 2)], { type: 'application/json' }))
    a.download = `${recipe.name.replace(/\s+/g, '-') || 'recipe'}.json`
    a.click()
  }

  if (!recipe) return <div className="boot">{error || 'Starting…'}</div>
  const font = fonts.find((f) => f.id === fontId)
  const lo = recipe.stops[0].position, hi = recipe.stops[recipe.stops.length - 1].position

  return (
    <div className="app" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); onFiles(e.dataTransfer.files) }}>
      <aside className="left">
        <header>
          <h1>font‑manipulator</h1>
          <p className="hint">Drop a font anywhere. Only use fonts whose licence allows modification.</p>
        </header>
        <section>
          <h3>Font</h3>
          <select value={fontId} onChange={(e) => setFontId(e.target.value)}>
            {fonts.map((f) => <option key={f.id} value={f.id}>{f.label} ({f.glyphs} glyphs){f.origin === 'upload' ? ' · uploaded' : ''}</option>)}
          </select>
          <label className="file">Upload OTF / TTF / WOFF2…
            <input type="file" accept=".otf,.ttf,.woff,.woff2" onChange={(e) => onFiles(e.target.files)} />
          </label>
          {font?.axes.length ? (
            <div className="pin">
              <p className="hint">Variable source — choose the instance to decay:</p>
              {font.axes.map((a) => (
                <label key={a.tag} className="param"><span>{a.tag}</span>
                  <input type="range" min={a.min} max={a.max} step={a.max - a.min > 20 ? 1 : 0.01}
                    value={pin[a.tag] ?? font.location[a.tag] ?? a.default}
                    onChange={(e) => setPin({ ...pin, [a.tag]: Number(e.target.value) })} />
                  <span className="num">{Number(pin[a.tag] ?? font.location[a.tag] ?? a.default).toFixed(0)}</span>
                </label>
              ))}
              <button className="small" onClick={async () => {
                const d = await api.pin(font.id, { ...font.location, ...pin })
                setFonts((fs) => [...fs.filter((x) => x.id !== d.id), d]); setFontId(d.id); setPin({})
              }}>use this instance</button>
            </div>
          ) : null}
        </section>
        <section>
          <h3>Recipe
            <button className="small dice" title="Random recipe for this font (keeps font, axis tag and export name)"
              onClick={() => {
                const rr = randomRecipe(effects, { output: recipe.output, quality: recipe.quality, axis: recipe.axis })
                setRecipe(rr); setActiveStop(Math.min(1, rr.stops.length - 1))
                setAxisValue(rr.stops[rr.stops.length - 1].position); setAnimate(false)
              }}>🎲 random</button>
          </h3>
          <select value="" onChange={(e) => {
            const p = presets.find((x) => x.id === e.target.value)
            if (p) { setRecipe(structuredClone(p.recipe)); setActiveStop(Math.min(1, p.recipe.stops.length - 1)); setAxisValue(p.recipe.axis.default ?? p.recipe.stops[0].position) }
          }}>
            <option value="">load preset…</option>
            {presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
          <input value={recipe.name} onChange={(e) => setRecipe({ ...recipe, name: e.target.value })} />
          <div className="row">
            <button className="small" onClick={saveRecipe}>save .json</button>
            <label className="file small">load .json<input type="file" accept=".json" onChange={(e) => loadRecipeFile(e.target.files)} /></label>
            <button className="small" onClick={() => api.defaultRecipe().then(setRecipe)}>reset</button>
          </div>
        </section>
        <RecipeEditor recipe={recipe} effects={effects} onChange={setRecipe} activeStop={activeStop} onSelectStop={setActiveStop} />
      </aside>

      <main>
        <div className="toolbar">
          <label className="axis">
            <span>{recipe.axis.tag}</span>
            <input type="range" min={lo} max={hi} step={0.1} value={axisValue}
              onChange={(e) => { setAnimate(false); setAxisValue(Number(e.target.value)) }} />
            <span className="num">{axisValue.toFixed(1)}</span>
          </label>
          <div className="stop-jumps">
            {recipe.stops.map((s, i) => (
              <button key={i} className="small" onClick={() => { setAnimate(false); setAxisValue(s.position) }}>{s.name}</button>
            ))}
          </div>
          <label className="check"><input type="checkbox" checked={animate} onChange={(e) => setAnimate(e.target.checked)} /> animate</label>
          <label className="size">size <input type="range" min={24} max={320} value={size} onChange={(e) => setSize(Number(e.target.value))} /></label>
          <div className="svg-export">
            <button className="small" disabled={svgBusy} onClick={() => downloadSvg(false)}
              title="Download the tester text as SVG outlines at the current slider position">⤓ SVG</button>
            <button className="small" disabled={svgBusy} onClick={() => downloadSvg(true)}
              title="Download one row per stop as SVG outlines">⤓ SVG · all stops</button>
          </div>
          <span className={`status ${busy ? 'busy' : ''}`}>{busy ? 'compiling…' : status}</span>
        </div>
        {error && <div className="error-bar">{error}</div>}
        <div className="tester" contentEditable suppressContentEditableWarning spellCheck={false}
          ref={(el) => { testerRef.current = el; if (el && !testerInit.current) { el.innerText = text; testerInit.current = true } }}
          style={{ fontFamily: previewFamily ? `"${previewFamily}"` : 'serif', fontSize: size,
                   fontVariationSettings: `'${recipe.axis.tag}' ${axisValue}` }}
          onInput={(e) => setText((e.target as HTMLElement).innerText)}
          onClick={() => {
            const sel = window.getSelection()
            if (sel?.focusNode?.textContent && sel.focusOffset > 0) setInspectChar(sel.focusNode.textContent[sel.focusOffset - 1])
          }} />
        <div className="waterfall" style={{ fontFamily: previewFamily ? `"${previewFamily}"` : 'serif' }}>
          {recipe.stops.map((s, i) => (
            <div key={i} style={{ fontVariationSettings: `'${recipe.axis.tag}' ${s.position}` }}>
              <small>{s.position} · {s.name}</small>{text}
            </div>
          ))}
        </div>
      </main>

      <aside className="right">
        <section>
          <h3>Inspect <input className="char" value={inspectChar} maxLength={1} onChange={(e) => setInspectChar(e.target.value)} /></h3>
          <Inspector glyph={glyph} recipe={recipe} axisValue={axisValue} font={font} />
        </section>
        <section>
          <h3>Export</h3>
          <label className="param"><span>Family name</span>
            <input value={recipe.output.family} onChange={(e) => setRecipe({ ...recipe, output: { family: e.target.value } })} />
          </label>
          <p className="hint">Don't reuse the source's reserved name (OFL fonts often reserve theirs).</p>
          <button disabled={!!jobStatus && jobStatus.stage !== 'done' && jobStatus.stage !== 'error'}
            onClick={async () => {
              setJobStatus({ stage: 'processing', done: 0, total: font?.glyphs ?? 0, elapsed: 0 })
              setJob((await api.exportStart(fontId, recipe)).job)
            }}>
            Build full variable font
          </button>
          {jobStatus && (
            <div className="job">
              {jobStatus.stage === 'processing' && <progress max={jobStatus.total} value={jobStatus.done} />}
              <div>{jobStatus.stage} · {jobStatus.done}/{jobStatus.total} glyphs · {jobStatus.elapsed}s</div>
              {jobStatus.error && <div className="error-bar">{jobStatus.error}</div>}
              {jobStatus.stage === 'done' && (
                <div className="downloads">
                  <a href={api.exportUrl(job, 'woff2')}>WOFF2 ({Math.round((jobStatus.sizes?.woff2 ?? 0) / 1024)} KB)</a>
                  <a href={api.exportUrl(job, 'ttf')}>TTF ({Math.round((jobStatus.sizes?.ttf ?? 0) / 1024)} KB)</a>
                  <a href={api.exportUrl(job, 'recipe')}>recipe.json</a>
                  {jobStatus.report?.missing.length ? <div className="error-bar">{jobStatus.report.missing.length} glyphs didn't vary</div> : null}
                </div>
              )}
            </div>
          )}
        </section>
      </aside>
    </div>
  )
}
