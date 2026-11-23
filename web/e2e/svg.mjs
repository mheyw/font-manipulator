import { chromium } from 'playwright-core'
import fs from 'node:fs'
const b = await chromium.launch({ executablePath: process.env.CHROME ?? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', headless: true })
const p = await b.newPage({ viewport: { width: 1500, height: 950 }, acceptDownloads: true })
const errors = []; p.on('pageerror', e => errors.push(String(e)))
await p.goto('http://127.0.0.1:8765/')
await p.waitForFunction(() => (document.querySelector('.status')?.textContent || '').includes('glyphs'), null, { timeout: 60000 })
await p.locator('section:has(h3:text("Recipe")) select').selectOption({ label: 'Motion smear' })
await p.waitForTimeout(400)
await p.locator('.tester').click(); await p.keyboard.press('Meta+A'); await p.keyboard.type('OCTANE')
await p.locator('.axis input[type=range]').fill('66')
await p.waitForTimeout(2500)
for (const [label, out] of [['⤓ SVG', '/tmp/svg-current.svg'], ['⤓ SVG · all stops', '/tmp/svg-all.svg']]) {
  const [dl] = await Promise.all([p.waitForEvent('download', { timeout: 120000 }), p.getByRole('button', { name: label, exact: true }).click()])
  await dl.saveAs(out)
  const s = fs.readFileSync(out, 'utf8')
  console.log(label, '->', dl.suggestedFilename(), s.length, 'bytes,', (s.match(/<path/g) || []).length, 'paths')
}
console.log('errors:', errors.length ? errors : 'none')
await b.close()
