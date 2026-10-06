// Screenshot one console page as a signed-in user.
// Usage: node shot.cjs <session-file> <out-dir> [path] [selector] [index]
//   path      page to open, default /journal/   (e.g. /circuits/, /journal/?match=39)
//   selector  element to wait for and screenshot, default .jrow (a journal row)
//   index     which match of the selector to screenshot, default 1
// Writes <out-dir>/page.png (full page) and <out-dir>/element.png, prints the
// match count, the element's text and any browser console errors.
// BASE_URL overrides http://127.0.0.1:8049.
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const fs = require('fs');
const path = require('path');

const [sessionFile, outDir, pagePath = '/journal/', selector = '.jrow', index = '1'] =
  process.argv.slice(2);
if (!sessionFile || !outDir) {
  console.error('usage: node shot.cjs <session-file> <out-dir> [path] [selector] [index]');
  process.exit(2);
}
const base = process.env.BASE_URL || 'http://127.0.0.1:8049';
const session = fs.readFileSync(sessionFile, 'utf8').trim();

(async () => {
  fs.mkdirSync(outDir, { recursive: true });
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  // Reduced motion makes the boot screen, power-on and trace animations render their
  // final state, so the screenshot does not catch them halfway.
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    reducedMotion: 'reduce',
  });
  await context.addCookies([
    { name: 'sessionid', value: session, domain: new URL(base).hostname, path: '/' },
  ]);
  const page = await context.newPage();
  const errors = [];
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  page.on('pageerror', (e) => errors.push(String(e)));
  try {
    await page.goto(base + pagePath);
    await page.waitForSelector(selector, { timeout: 20000 });
    await page.waitForLoadState('networkidle'); // the trace loads after the list
    await page.waitForTimeout(1500);
    await page.screenshot({ path: path.join(outDir, 'page.png'), fullPage: true });
    const matches = page.locator(selector);
    const el = matches.nth(Number(index));
    await el.screenshot({ path: path.join(outDir, 'element.png') });
    console.log('url', page.url());
    console.log('count', await matches.count());
    console.log('text', (await el.innerText()).replace(/\n/g, ' | '));
  } catch (e) {
    await page.screenshot({ path: path.join(outDir, 'page.png'), fullPage: true });
    console.log('failed', String(e), 'url', page.url());
    process.exitCode = 1;
  }
  console.log('errors', JSON.stringify(errors));
  await browser.close();
})();
