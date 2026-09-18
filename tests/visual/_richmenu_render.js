// Renders the LINE rich menu artwork to a PNG at exactly the size LINE
// demands. Not a check — it asserts nothing — which is what the leading
// underscore says; it lives here so `require("playwright")` resolves the
// same way it does for every check beside it.
//
// The artwork is HTML rather than a drawing so it stays editable and
// regenerable: changing a label is a text edit, not a trip through an
// image editor, and the colours come from the same tokens as the app.
//
//   node tests/visual/_richmenu_render.js <source.html> <out.png>

const { chromium } = require("playwright");
const path = require("path");

// The rich menu's compact size by default. Overridable because the same
// renderer now also produces the Official Account's profile picture,
// which is square — see _avatar.html.
const WIDTH = Number(process.env.SHOT_WIDTH || 2500);
const HEIGHT = Number(process.env.SHOT_HEIGHT || 843);

const source = process.argv[2];
const out = process.argv[3];
if (!source || !out) {
  console.error("usage: node tests/visual/_richmenu_render.js <source.html> <out.png>");
  process.exit(2);
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  // Screenshotting the viewport, not the page: LINE rejects an image
  // that is even a pixel off 2500x843, and fullPage would follow the
  // document's own height instead.
  const page = await browser.newPage({ viewport: { width: WIDTH, height: HEIGHT } });
  try {
    await page.goto("file:///" + path.resolve(source).replace(/\\/g, "/"));
    // The face arrives over the network. Screenshotting before it lands
    // silently bakes in the fallback, and nobody notices until the menu
    // is already on everyone's phone.
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(600);
    await page.screenshot({ path: out });
    const box = await page.evaluate(() => ({
      w: document.body.scrollWidth,
      h: document.body.scrollHeight,
    }));
    const ok = box.w === WIDTH && box.h === HEIGHT;
    console.log(
      `body ${box.w}x${box.h} ${
        ok ? `— matches the ${WIDTH}x${HEIGHT} asked for` : "— WRONG SIZE, LINE will refuse it"
      }`
    );
    console.log(`written: ${out}`);
    process.exit(ok ? 0 : 1);
  } finally {
    await browser.close();
  }
})();
