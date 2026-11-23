import { chromium } from 'playwright-core'
const CHROME = process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
const b = await chromium.launch({ executablePath: CHROME, headless: true })
const p = await b.newPage({ viewport: { width: 1500, height: 950 } })
const errors = []
p.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
p.on('pageerror', e => errors.push(String(e)))
const log = (...a) => console.log('•', ...a)
const status = () => p.locator('.status').innerText()
const waitPreview = async (prev) => { await p.waitForFunction(pv => { const s = document.querySelector('.status')?.textContent || ''; return s.includes('glyphs') && s !== pv }, prev, { timeout: 60000 }) }
const errBar = async () => (await p.locator('.error-bar').allInnerTexts()).join(' | ')

await p.goto('http://127.0.0.1:8765/')
await p.waitForFunction(() => (document.querySelector('.status')?.textContent || '').includes('glyphs'), null, { timeout: 60000 })
log('1 loaded:', await status())

await p.locator('.axis input[type=range]').fill('50')
log('2 slider ->', await p.locator('.tester').evaluate(e => e.style.fontVariationSettings))

let s = await status()
const softness = p.locator('.effect .param').filter({ hasText: 'Softness' }).locator('input[type=number]')
await softness.fill('40'); await waitPreview(s); log('3 param edit -> preview:', await status())

s = await status()
await p.locator('.tester').click(); await p.keyboard.press('Meta+A'); await p.keyboard.type('Hello Ferro 2026')
await waitPreview(s); log('4 typed -> preview:', await status(), '| text:', await p.locator('.tester').innerText())

await p.locator('.inspect, h3:has-text("Inspect") input.char').fill('H')
await p.waitForFunction(() => document.querySelector('.inspector-head b')?.textContent === 'H', null, { timeout: 30000 })
log('5 inspector glyph:', await p.locator('.inspector-head').innerText(), '| masters:', await p.locator('.masters figure').count())
await p.screenshot({ path: 'e2e/ui-1.png' })

s = await status()
await p.getByRole('button', { name: '+ stop' }).click()
await p.locator('select.add-effect').selectOption('magnet')
await waitPreview(s)
log('6 added stop + magnet:', await p.locator('.stop').count(), 'stops |', await status(), '| errors:', await errBar())
await p.locator('.axis input[type=range]').fill('125')
await p.screenshot({ path: 'e2e/ui-2.png' })

// variable font pinning
const fr = await p.locator('section:has(h3:text("Font")) select').locator('option', { hasText: 'Fraunces' }).getAttribute('value')
s = await status()
const cur = await p.locator('section:has(h3:text("Font")) select').inputValue()
await p.locator('section:has(h3:text("Font")) select').selectOption(fr)
if (cur !== fr) await waitPreview(s)
log('7 Fraunces default instance:', await p.locator('section:has(h3:text("Font")) select option:checked').innerText(), '|', await status())
await p.locator('.pin .param').filter({ hasText: 'opsz' }).locator('input[type=range]').fill('144')
await p.locator('.pin .param').filter({ hasText: 'wght' }).locator('input[type=range]').fill('400')
s = await status()
await p.getByRole('button', { name: 'use this instance' }).click()
await waitPreview(s)
log('8 pinned:', await p.locator('section:has(h3:text("Font")) select option:checked').innerText(), '|', await status())
await p.locator('.axis input[type=range]').fill('0')
await p.screenshot({ path: 'e2e/ui-3.png' })

// export
await p.locator('.param').filter({ hasText: 'Family name' }).locator('input').fill('E2E Serif Decay')
await p.getByRole('button', { name: 'Build full variable font' }).click()
log('9 export button disabled right after click:', await p.getByRole('button', { name: 'Build full variable font' }).isDisabled())
await p.locator('.downloads a').first().waitFor({ timeout: 300000 })
const href = await p.locator('.downloads a').first().getAttribute('href')
const r = await p.request.get('http://127.0.0.1:8765' + href)
log('10 export done:', await p.locator('.job').innerText().then(t => t.replace(/\n/g, ' ')), '| download bytes', (await r.body()).length, r.headers()['content-disposition'])
s = await status()
await p.locator('section:has(h3:text("Recipe")) select').selectOption({ label: 'Melt drip' })
await waitPreview(s)
log('11 preset loaded:', await p.locator('.stop').count(), 'stops |', await status())
await p.screenshot({ path: 'e2e/ui-preset.png' })
log('console errors:', errors.length ? errors : 'none')
await b.close()
