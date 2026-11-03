const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require("playwright");
const root = path.resolve(__dirname, '..');
const configs = [
  { slug: 'a-precision-lab', actions: ['[data-feedback].primary', '[data-feedback]:not(.primary)', '#demo-toggle', '#export-button'], feedback: '#feedback', checkbox: '[data-channel="laser"]', series: '[data-series="laser"]' },
  { slug: 'b-control-room', actions: ['#connect', '#disconnect', '#demo', '#clear'], feedback: '#toast', checkbox: '[data-trace="tf"]', series: '.trace-tf' },
  { slug: 'c-spatial-compass', actions: ['[data-preview="connect"]', '[data-preview="disconnect"]', '[data-preview="demo"]', '[data-preview="csv"]'], feedback: '.toast', checkbox: '[data-curve="tf"]', series: '.curve-tf' },
];

async function main() {
  const browser = await chromium.launch({ headless: true, channel: 'chrome' });
  const results = [];
  try {
    for (const config of configs) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
      const pageErrors = [], network = [];
      page.on('pageerror', error => pageErrors.push(error.message));
      page.on('request', request => network.push(request.url()));
      await page.addInitScript(() => {
        window.__hardwareAccess = [];
        for (const api of ['serial', 'usb']) Object.defineProperty(navigator, api, { configurable: true, get() { window.__hardwareAccess.push(api); throw new Error('Hardware API access blocked by isolated design QA'); } });
      });
      await page.goto(`http://127.0.0.1:8289/opendesign/mockups/${config.slug}/index.html`);
      await page.evaluate(() => document.fonts.ready);
      const bodyText = await page.locator('body').innerText();
      const checks = {
        baselineValues: ['128.4', '46.2', '864', '1284'].every(text => bodyText.includes(text)),
        baselineUnits: await page.locator('[data-unit="mm"]').getAttribute('aria-pressed') === 'true' && await page.locator('[data-unit="cm"]').getAttribute('aria-pressed') === 'false',
        demoLabeled: bodyText.includes('演示'),
        asynchronousDisclosure: /异步|不同步/.test(bodyText),
        thirtySecondPlot: /30\s*(s|秒)/.test(bodyText),
      };
      const feedback = [];
      for (const selector of config.actions) {
        await page.locator(selector).click();
        feedback.push({ selector, text: await page.locator(config.feedback).innerText() });
      }
      checks.actionFeedback = feedback.every(item => item.text.length > 0);
      await page.locator(config.checkbox).uncheck();
      checks.channelHidden = await page.locator(config.series).first().evaluate(element => { const style = getComputedStyle(element); return style.visibility === 'hidden' || style.display === 'none'; });
      await page.locator(config.checkbox).check();
      checks.channelRestored = await page.locator(config.series).first().evaluate(element => { const style = getComputedStyle(element); return style.visibility !== 'hidden' && style.display !== 'none'; });
      await page.locator('[data-unit="cm"]').click();
      const afterCm = await page.locator('body').innerText();
      checks.unitDoesNotAffectFront = afterCm.includes('46.2');
      await page.locator('[data-unit="mm"]').click();
      checks.mmRestored = (await page.locator('body').innerText()).includes('128.4');
      const details = page.locator('details');
      checks.diagnosticsInitiallyCollapsed = !(await details.evaluate(element => element.open));
      await page.locator('summary').click();
      checks.diagnosticsExpand = await details.evaluate(element => element.open);
      checks.diagnosticsContainRecords = await page.locator('details pre').isVisible();
      await page.locator('summary').click();
      checks.diagnosticsCollapse = !(await details.evaluate(element => element.open));
      if (config.slug === 'b-control-room') {
        await page.locator('#connection-mode').selectOption({ index: 1 });
        checks.modeFeedback = (await page.locator('#toast').innerText()).includes('未向设备发送命令');
        await page.locator('#csv').click();
        checks.csvDialogOpens = await page.locator('#csv-dialog').evaluate(element => element.open);
        await page.locator('#close-dialog').click();
        checks.csvDialogCloses = !(await page.locator('#csv-dialog').evaluate(element => element.open));
      }
      await page.setViewportSize({ width: 390, height: 844 });
      await page.reload();
      await page.evaluate(() => document.fonts.ready);
      const mobile = await page.evaluate(() => {
        const visible = element => element.checkVisibility({ visibilityProperty: true }) && element.getBoundingClientRect().width > 0;
        const controls = [...document.querySelectorAll('button,summary,select,.legend label')].filter(visible).map(element => { const box = element.getBoundingClientRect(); return { text: element.textContent.trim(), w: box.width, h: box.height }; });
        const table = document.querySelector('.table-wrap');
        return { overflow: document.documentElement.scrollWidth > innerWidth, controls, undersized: controls.filter(item => item.w < 44 || item.h < 44), table: table ? { clientWidth: table.clientWidth, scrollWidth: table.scrollWidth } : null };
      });
      checks.noMobileOverflow = !mobile.overflow;
      checks.mobileTouchTargets44 = mobile.undersized.length === 0;
      checks.noHardwareAccess = (await page.evaluate(() => window.__hardwareAccess)).length === 0;
      checks.noExternalRequests = network.every(url => url.startsWith('http://127.0.0.1:8289/'));
      checks.noPageErrors = pageErrors.length === 0;
      results.push({ slug: config.slug, checks, feedback, mobile, pageErrors, network });
      await page.close();
    }
    await fs.writeFile(path.join(__dirname, 'interaction-checks.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results.map(({ slug, checks, mobile }) => ({ slug, checks, undersized: mobile.undersized, table: mobile.table })), null, 2));
  } finally { await browser.close(); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
