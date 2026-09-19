// Capture email screenshots from the running app.
//
//   node capture_screenshots.js <e2e dir> <out dir> <spec.json>
//
// <e2e dir> is the repo's e2e/ folder (its node_modules has Playwright).
// spec.json is a list, one entry per screenshot:
//   { "file": "week-view.png",          // written into <out dir>
//     "url": "/week",                   // path on the app
//     "width": 800,                     // viewport width, see SKILL.md
//     "from": ".ledger__header",        // clip starts at the top of this element
//     "to": ".calendar-week" }          // ...and ends at the bottom of this one
// Use "selector" instead of from/to to clip to one element with padding:
//   { "file": "toggle.png", "url": "/week", "width": 800,
//     "selector": ".ledger__nav", "pad": [40, 20] }
//
// APP_URL (default http://localhost:5051), APP_EMAIL and APP_PASSWORD (the
// seeded dev admin) can be overridden from the environment.
const path = require('path');
const fs = require('fs');
const { chromium } = require(path.resolve(process.argv[2], 'node_modules/playwright'));
const out = path.resolve(process.argv[3]);
const spec = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const base = process.env.APP_URL || 'http://localhost:5051';

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 }, deviceScaleFactor: 2 });
  const page = await ctx.newPage();

  await page.goto(base + '/login');
  await page.fill('input[type=email]', process.env.APP_EMAIL || 'admin@example.invalid');
  await page.fill('input[type=password]', process.env.APP_PASSWORD || 'changeme');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);

  for (const shot of spec) {
    await page.setViewportSize({ width: shot.width || 1000, height: 900 });
    await page.goto(base + shot.url);
    let clip;
    if (shot.selector) {
      const box = await page.locator(shot.selector).boundingBox();
      const [px, py] = shot.pad || [0, 0];
      clip = { x: box.x - px, y: box.y - py, width: box.width + 2 * px, height: box.height + 2 * py };
    } else {
      const top = await page.locator(shot.from).boundingBox();
      const end = await page.locator(shot.to).boundingBox();
      clip = { x: top.x, y: top.y, width: top.width, height: end.y + end.height - top.y + 2 };
    }
    await page.screenshot({ path: path.join(out, shot.file), clip });
    console.log('wrote', shot.file);
  }
  await browser.close();
})();
