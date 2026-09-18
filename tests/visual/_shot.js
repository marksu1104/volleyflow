// Screenshots one page of the running local app, at phone width.
//
// Not a check — it asserts nothing, which is what the leading underscore
// says, the same as _richmenu_render.js. It lives here so
// require("playwright") resolves the way it does for every check beside
// it, and so that "show me the current screen" never has to be answered
// by redrawing the screen from memory. A proposal compared against a
// remembered version of the current design is a proposal compared
// against nothing.
//
//   node tests/visual/_shot.js <out.png> <url> [waitSelector] [clickSelector]
//
// The url is taken as given, so ?as= and any other parameter are the
// caller's business. waitSelector holds the shot until the page has
// actually rendered; without it a screenshot races the spinner and
// records the loading state, which is exactly the thing being redesigned.
//
// VF_CLUB=<id> in the environment seeds the remembered club before the
// page loads, the way loadtime.js and richmenulinks.js do. Without it
// every shot is of a first-time reader, which is a real and useful
// state — it is how the stranded ledger sheet was found — but it is not
// the state most people are in, and a comparison that only ever shows
// the current design at its worst is not an honest comparison.

const { chromium } = require("playwright");

const [out, url, waitSelector, clickSelector] = process.argv.slice(2);
if (!out || !url) {
  console.error(
    "usage: node tests/visual/_shot.js <out.png> <url> [waitSelector] [clickSelector]"
  );
  process.exit(2);
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  const context = await browser.newContext({
    viewport: { width: 390, height: 900 },
    deviceScaleFactor: 2,
  });
  if (process.env.VF_CLUB) {
    await context.addInitScript(
      (id) => localStorage.setItem("vf_club", String(id)),
      process.env.VF_CLUB
    );
  }
  const page = await context.newPage();
  try {
    await page.goto(url);
    if (waitSelector) await page.waitForSelector(waitSelector, { timeout: 30000 });
    if (clickSelector) {
      await page.click(clickSelector);
      // Sheets animate in; shooting mid-transition records a half-open
      // panel that misrepresents the design being compared against.
      await page.waitForTimeout(900);
    }
    // Settle: fonts and any second SWR paint. getJsonSWR fires its
    // callback twice (cache, then network), so an early shot can catch
    // the cached figures rather than the real ones.
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(700);
    await page.screenshot({ path: out, fullPage: true });
    const size = await page.evaluate(() => ({
      w: document.documentElement.scrollWidth,
      h: document.documentElement.scrollHeight,
    }));
    console.log(`written: ${out}  (${size.w}x${size.h})`);
  } finally {
    await browser.close();
  }
})();
