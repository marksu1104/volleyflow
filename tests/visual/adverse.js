// The app on a bad day: a slow network, and a write the server refuses.
//
// Everything else here measures the app on a fast local connection, where
// a write lands in well under a second. A phone on mobile data is not
// that, and the bugs that hurt most — a change that flips back, a spinner
// that never clears, a screen that ends up disagreeing with the server —
// all need the gap between tap and answer to be wide enough to act in.
// This widens it on purpose, using Chromium's own request interception,
// and then acts inside it.
//
//     .\scripts\dev-api.ps1      (in one window)
//     .\scripts\dev-web.ps1      (in another)
//     uv run python scripts/seed_dev.py
//     node tests/visual/adverse.js
//
// Destructive: local seed data only, and re-seed afterwards.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const as = (name) => encodeURIComponent(name);

/** What the hero says about the viewer right now. */
const STANCE = `(() => {
  const labels = [...document.querySelectorAll("#hero-wrap .hact")].map((b) => b.textContent.trim());
  return labels.some((l) => l === "取消請假") ? "absent" : "playing";
})()`;

const TAP_LEAVE = `(() => {
  const b = [...document.querySelectorAll("#hero-wrap .hact")]
    .find((x) => /^請假$|^取消請假$/.test(x.textContent.trim()));
  if (!b) return null;
  const was = b.textContent.trim();
  b.click();
  return was;
})()`;

async function open(browser, who, page_name) {
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 140)));
  page.on("dialog", (d) => d.accept("1"));
  await page.goto(`${BASE}/${page_name || "member.html"}?as=${as(who)}`, {
    waitUntil: "networkidle",
  });
  await page.waitForFunction(
    () => typeof currentSeason !== "undefined" && currentSeason && currentSeason.games.length,
    null,
    { timeout: 25000 }
  );
  await page.waitForTimeout(600);
  return { page, errors };
}

/** Holds every API write for `ms` before letting it through.
 *
 * Returns a handle whose `.ms` can be set to 0 to give the network back.
 * Removing the route with unroute() instead crashes Playwright when a
 * held request is still in the air — it tries to continue a route that
 * has already been torn down. */
async function slowWrites(page, ms) {
  const handle = { ms };
  await page.route("**/*", async (route) => {
    const request = route.request();
    const isWrite = request.method() !== "GET" && request.url().includes(":8000");
    if (isWrite && handle.ms) await new Promise((r) => setTimeout(r, handle.ms));
    // The page can be closed while a request is held; continuing then is
    // not a failure of the app, so it is swallowed rather than thrown.
    await route.continue().catch(() => {});
  });
  return handle;
}

/** Answers every API write with a refusal, without touching the server. */
async function refuseWrites(page) {
  await page.route("**/*", async (route) => {
    const request = route.request();
    if (request.method() !== "GET" && request.url().includes(":8000")) {
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ detail: "測試用的失敗" }),
      });
      return;
    }
    await route.continue();
  });
}

const report = (label, problems) => {
  console.log((problems.length ? "FAIL " : "ok   ") + label + (problems.length ? " — " + problems.join("、") : ""));
  return problems.length ? 1 : 0;
};

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  let failed = 0;

  // 1. Two taps inside one slow round trip. The screen must follow the
  // taps and stay where the second one left it.
  {
    const { page, errors } = await open(browser, "蘇懂");
    const net = await slowWrites(page, 2500);
    const first = await page.evaluate(TAP_LEAVE);
    await page.waitForTimeout(400);
    const second = await page.evaluate(TAP_LEAVE);
    const settledOn = await page.evaluate(STANCE);
    const wobbles = [];
    for (let waited = 0; waited < 12000; waited += 100) {
      const now = await page.evaluate(STANCE);
      if (now !== settledOn) wobbles.push(`${waited}ms 變成 ${now}`);
      await page.waitForTimeout(100);
    }
    failed += report(
      `2.5 秒延遲下連按「${first}」「${second}」，畫面不翻回去`,
      wobbles.slice(0, 3).concat(errors)
    );

    // And the server has to agree with the screen once everything lands.
    net.ms = 0;
    const server = await page.evaluate(async () => {
      const detail = await (await fetch(`${API_BASE}/seasons/${currentSeason.id}`, { headers: authHeader() })).json();
      const game = detail.games.find((g) => g.id === selectedGameId);
      return game.absences.some((a) => a.player_name === playerName()) ? "absent" : "playing";
    });
    failed += report(
      "慢速操作結束後，伺服器跟畫面說的一樣",
      server === settledOn ? [] : [`畫面說 ${settledOn}，伺服器說 ${server}`]
    );
    await page.close();
  }

  // 2. A write the server refuses. The optimistic change must come back
  // off, and the reason must be on screen — not a silent wrong state.
  {
    const { page, errors } = await open(browser, "蘇懂");
    const before = await page.evaluate(STANCE);
    await refuseWrites(page);
    await page.evaluate(TAP_LEAVE);
    await page.waitForTimeout(2500);
    const after = await page.evaluate(STANCE);
    const toasted = await page.evaluate(
      () => [...document.querySelectorAll(".toast")].map((t) => t.textContent).join(" | ")
    );
    const problems = [];
    if (after !== before) problems.push(`畫面停在 ${after}，伺服器其實沒接受`);
    if (!toasted.includes("失敗")) problems.push("沒有告訴使用者失敗了");
    failed += report("伺服器拒絕時，畫面會退回去並說明原因", problems.concat(errors));
    await page.close();
  }

  // 3. Nothing may be left spinning, and nothing left disabled, once the
  // dust settles — on a slow network the busy state is actually visible,
  // so this is where a leaked one would show.
  {
    const { page, errors } = await open(browser, "蘇懂", "organizer.html");
    const net = await slowWrites(page, 1500);
    await page.evaluate(() => {
      const g = currentSeason.games.find((x) => !describeDate(x.date).isPast) || currentSeason.games[0];
      selectGame(g.id);
      openGameSheet();
    });
    await page.waitForTimeout(500);
    for (let i = 0; i < 6; i += 1) {
      await page.evaluate(() => {
        const b = document.querySelector("#game-detail [data-mark-absent], #game-detail [data-undo-absence]");
        if (b && !b.disabled) b.click();
      });
      await page.waitForTimeout(200);
    }
    net.ms = 0;
    await page.waitForTimeout(8000);
    const stuck = await page.evaluate(() => ({
      busy: document.querySelectorAll(".is-busy, [aria-busy='true']").length,
      disabled: [...document.querySelectorAll("#game-detail button")].filter((b) => b.disabled).length,
      chip: !!document.querySelector("#vf-pending.shown"),
    }));
    const problems = [];
    if (stuck.busy) problems.push(`${stuck.busy} 個按鈕還在轉圈`);
    if (stuck.disabled) problems.push(`${stuck.disabled} 個按鈕還是停用的`);
    if (stuck.chip) problems.push("「儲存中」沒有消失");
    failed += report("慢速下亂按之後，沒有卡住的轉圈或停用按鈕", problems.concat(errors));
    await page.close();
  }

  await browser.close();
  console.log(
    failed ? `\n${failed} 項有問題。` : "\n網路很慢或伺服器拒絕時，畫面都還是誠實的。"
  );
  process.exit(failed ? 1 : 0);
})();
