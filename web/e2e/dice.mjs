import { chromium } from 'playwright-core'
const b = await chromium.launch({ executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const p = await b.newPage({ viewport: { width: 1500, height: 950 } })
const errors = []; p.on('pageerror', e => errors.push(String(e))); p.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
await p.goto('http://127.0.0.1:8765/')
await p.waitForFunction(() => (document.querySelector('.status')?.textContent || '').includes('glyphs'), null, { timeout: 60000 })
const inter = await p.locator('section:has(h3:text("Font")) select option', { hasText: 'Inter' }).first().getAttribute('value')
await p.locator('section:has(h3:text("Font")) select').selectOption(inter)
await p.waitForTimeout(500)
for (let i = 1; i <= 12; i++) {
  const prev = await p.locator('.status').innerText()
  await p.getByRole('button', { name: /random/ }).click()
  try {
    await p.waitForFunction(pv => { const s = document.querySelector('.status')?.textContent || ''; return s.includes('glyphs') && s !== pv }, prev, { timeout: 90000 })
  } catch { console.log(i, 'TIMEOUT') }
  const name = await p.locator('section:has(h3:text("Recipe")) input').first().inputValue()
  const fx = await p.locator('.stop small').allInnerTexts()
  const err = (await p.locator('.error-bar').allInnerTexts()).join('|')
  console.log(i, name, '| fx/stop', fx.join(','), '|', await p.locator('.status').innerText(), err ? '| ERR ' + err : '')
  if (i <= 4) await p.screenshot({ path: `/tmp/dice-${i}.png` })
}
console.log('console errors:', errors.length ? errors.slice(0, 3) : 'none')
await b.close()
