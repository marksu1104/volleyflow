// Two people using the same game at once, tapping faster than the
// network answers, and then checking the screen still tells the truth.
//
// The API-level version of this lives in tests/api/test_chaos.py. This
// one exists because the browser adds its own failure modes on top: an
// optimistic update that never reconciles, a spinner that never clears,
// a list that shows somebody twice because two repaints raced.
//
//     .\scripts\dev-api.ps1      (in one window)
//     .\scripts\dev-web.ps1      (in another)
//     uv run python scripts/seed_dev.py
//     node tests/visual/chaos.js
//
// Destructive: point it only at a local server holding seed data.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const as = (name) => encodeURIComponent(name);

/** Everything that must be true of a game however it was hammered. */
const INVARIANTS = `(() => {
  const g = currentSeason.games.find((x) => x.id === selectedGameId);
  const absent = new Set(g.absences.map((a) => a.player_name));
  const playing = currentSeason.members
    .filter((m) => !absent.has(m.name)).map((m) => m.name)
    .concat(g.confirmed_drop_ins.map((d) => d.player_name));
  const queued = g.waitlist_entries.map((w) => w.player_name);
  const problems = [];
  if (playing.length !== new Set(playing).size) problems.push("同一個人在場上出現兩次");
  if (playing.some((p) => queued.includes(p))) problems.push("同時在場上與候補");
  if (queued.length !== new Set(queued).size) problems.push("候補名單有重複");
  if (playing.length > currentSeason.capacity) problems.push(playing.length + " 人超過上限 " + currentSeason.capacity);
  // What the screen says must match what the data says.
  const shown = Number((document.querySelector(".count-big") || {}).textContent || 0);
  if (shown !== playing.length) problems.push("畫面顯示 " + shown + " 人，資料是 " + playing.length + " 人");
  const stuck = document.querySelectorAll(".is-busy").length;
  if (stuck) problems.push(stuck + " 個按鈕還卡在載入中");
  return { problems, playing, queued, capacity: currentSeason.capacity };
})()`;

async function open(browser, page_name, who) {
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e).slice(0, 140)));
  page.on("dialog", (d) => d.accept("1"));
  await page.goto(`${BASE}/${page_name}?as=${as(who)}`, { waitUntil: "networkidle" });
  await page.waitForFunction(
    () => typeof currentSeason !== "undefined" && currentSeason && currentSeason.games.length,
    null,
    { timeout: 25000 }
  );
  await page.evaluate(() => {
    const g = currentSeason.games.find((x) => !describeDate(x.date).isPast) || currentSeason.games[0];
    selectGame(g.id);
    openGameSheet();
  });
  await page.waitForTimeout(700);
  return { page, errors, who };
}

/** Taps whatever is on screen, as fast as the browser will allow. */
async function hammer(page, selectors, rounds) {
  for (let i = 0; i < rounds; i += 1) {
    for (const sel of selectors) {
      await page
        .evaluate((s) => {
          const el = document.querySelector(s);
          if (el && !el.disabled) el.click();
        }, sel)
        .catch(() => {});
      await page.waitForTimeout(40); // faster than any round trip
    }
  }
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  let failed = 0;
  const report = (label, problems) => {
    console.log((problems.length ? "FAIL " : "ok   ") + label +
      (problems.length ? " — " + problems.join("、") : ""));
    if (problems.length) failed += 1;
  };

  // 1. One person, tapping much faster than the server answers.
  const solo = await open(browser, "organizer.html", "蘇懂");
  await hammer(solo.page, [
    "#game-detail [data-mark-absent]",
    '#game-detail [data-gd-tab="absent"]',
    "#game-detail [data-undo-absence]",
    '#game-detail [data-gd-tab="attending"]',
  ], 6);
  await solo.page.waitForTimeout(3500);
  const soloState = await solo.page.evaluate(INVARIANTS);
  report("一個人狂點請假／取消請假", soloState.problems.concat(solo.errors));

  // 2. The exact sequence reported as 「按了成功結果又切回去又切回來」:
  // take leave, then change your mind before the first change has
  // finished saving. Measured on the real page before the fix, the screen
  // was right at 316ms, wrong from 1257ms, and right again at 2017ms —
  // the refresh after the first write had overtaken the second write and
  // come back describing a roster from before it.
  //
  // Asserted by watching rather than by sampling the end: the end was
  // always correct. What was wrong was the half-second in the middle.
  const flipper = await open(browser, "member.html", "蘇懂");
  const takeLeave = `(() => {
    const b = [...document.querySelectorAll("#hero-wrap .hact")]
      .find((x) => /^請假|^取消請假/.test(x.textContent.trim()));
    if (!b) return null;
    b.click();
    return b.textContent.trim();
  })()`;
  const stance = `(() => {
    const labels = [...document.querySelectorAll("#hero-wrap .hact")].map((b) => b.textContent.trim());
    return labels.some((l) => l === "取消請假") ? "absent" : "playing";
  })()`;

  const firstTap = await flipper.page.evaluate(takeLeave);
  await flipper.page.waitForTimeout(300);
  const secondTap = await flipper.page.evaluate(takeLeave);
  const settledOn = await flipper.page.evaluate(stance);
  const wobbles = [];
  for (let waited = 0; waited < 6000; waited += 50) {
    const now = await flipper.page.evaluate(stance);
    if (now !== settledOn) wobbles.push(`${waited}ms 變成 ${now}`);
    await flipper.page.waitForTimeout(50);
  }
  report(
    `連按「${firstTap}」「${secondTap}」之後畫面不會自己翻回去`,
    firstTap && secondTap
      ? wobbles.slice(0, 4).concat(flipper.errors)
      : ["找不到請假按鈕，這項沒有測到"]
  );

  // 3. Two people on the same game at the same time.
  const organizer = await open(browser, "organizer.html", "蘇懂");
  // 阿哲 rather than a name off the roster: a roster entry the organizer
  // typed has no account behind it, so signing in as that name makes a
  // second person who is not in the club and sees nothing. See
  // scripts/seed_dev.py.
  const member = await open(browser, "member.html", "阿哲");
  await Promise.all([
    hammer(organizer.page, [
      "#game-detail [data-mark-absent]",
      "#game-detail [data-remove-drop-in]",
    ], 5),
    hammer(member.page, [
      "#hero-wrap .hact",
      '#game-detail [data-gd-tab="queued"]',
      '#game-detail [data-gd-tab="attending"]',
    ], 5),
  ]);
  await organizer.page.waitForTimeout(4000);
  await organizer.page.reload({ waitUntil: "networkidle" });
  await organizer.page.waitForFunction(
    () => typeof currentSeason !== "undefined" && currentSeason && currentSeason.games.length,
    null,
    { timeout: 25000 }
  );
  await organizer.page.evaluate(() => {
    const g = currentSeason.games.find((x) => !describeDate(x.date).isPast) || currentSeason.games[0];
    selectGame(g.id);
    openGameSheet();
  });
  await organizer.page.waitForTimeout(900);
  const shared = await organizer.page.evaluate(INVARIANTS);
  report("兩個人同時操作同一場", shared.problems.concat(organizer.errors, member.errors));
  console.log(`     場上 ${shared.playing.length}/${shared.capacity} 人・候補 ${shared.queued.length} 人`);

  // 4. A burst of 已收 on the money screen. Same failure as 2, on a page
  // that had its own, unqueued copy of the write path: five taps sent
  // five requests at once, each asking for a re-read as it landed, so a
  // read could describe the books as they were three payments ago. Rows
  // went back to 未收 for about half a second and then forward again.
  // Measured here as "a row the screen had marked paid asks for money
  // again", which is what a person sees and no unit test can reach.
  const money = await browser.newPage({ viewport: { width: 390, height: 900 } });
  const moneyErrors = [];
  money.on("pageerror", (e) => moneyErrors.push(String(e).slice(0, 140)));
  money.on("dialog", (d) => d.accept("1"));
  await money.goto(`${BASE}/organizer-ledger.html?as=${as("蘇懂")}`, {
    waitUntil: "networkidle",
  });
  await money.waitForTimeout(2500);
  const stillOwing = () =>
    money.evaluate(() => document.querySelectorAll("#tab-fee .m-net.owe").length);

  const owingAtStart = await stillOwing();
  const seen = [];
  for (let i = 0; i < 5; i += 1) {
    await money.evaluate(() => {
      const b = document.querySelector("#tab-fee button.paid");
      if (b) b.click();
    });
    seen.push(await stillOwing());
    await money.waitForTimeout(120);
  }
  for (let waited = 0; waited < 8000; waited += 100) {
    seen.push(await stillOwing());
    await money.waitForTimeout(100);
  }
  const wentBackwards = seen.filter((n, i) => i > 0 && n > seen[i - 1]).length;
  report(
    "連續按「已收」之後不會有已收的列又變回未收",
    owingAtStart < 2
      ? ["這一季沒有足夠的未收款項，這項沒有測到"]
      : (wentBackwards
          ? [`${wentBackwards} 次倒退：${seen.slice(0, 30).join(",")}`]
          : []
        ).concat(moneyErrors)
  );
  console.log(`     未收 ${owingAtStart} -> ${seen[seen.length - 1]} 列`);

  await browser.close();
  console.log(failed ? `\n${failed} 項有問題。` : "\n亂按之後畫面與資料仍然一致，沒有卡住的按鈕。");
  process.exit(failed ? 1 : 0);
})();
