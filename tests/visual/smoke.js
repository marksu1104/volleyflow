// Presses every button on every page and reports the ones that do
// nothing.
//
// This exists because of a bug that no static check could see: the game
// sheet's tab handler matched a data attribute the container itself
// carried, so closest() walked up from any button in the sheet and every
// 請假 / 移除 / 遞補 / 指定代打 tap was swallowed as a tab switch. The
// markup was right, the handler was attached, the tests passed — and
// nothing happened when you pressed it.
//
// A control counts as doing something if it changes the page's HTML,
// navigates, opens a dialog, or sends a request. That is a heuristic:
// a control already in the state it sets (the tab you are on, the air
// conditioning switch already at 開) correctly does nothing, so read the
// list rather than trusting the count.
//
//     .\scripts\dev-api.ps1      (in one window)
//     .\scripts\dev-web.ps1      (in another)
//     uv run python scripts/seed_dev.py
//     node tests/visual/smoke.js
//
// It clicks destructive controls too, so it must only ever be pointed at
// a local server holding seed data — re-run seed_dev.py afterwards.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const AS = encodeURIComponent("蘇懂");
// Ordered so the destructive page runs last. Auditing the roster screen
// presses every 移除 on it, which leaves the later pages with an empty
// season and a handful of controls to test instead of a full one.
const PAGES = [
  "member.html",
  "organizer.html",
  "organizer-ledger.html",
  "organizer-settings.html",
  "profile.html",
  "organizer-members.html",
];

// Pressing these ends the run rather than testing anything: they throw
// away the very data every later page needs.
const SKIP = [/刪除/, /結算/, /登出/, /移出球隊/];

// Controls whose job is to put the page into a state it may already be
// in, so "nothing happened" is the correct outcome and not a finding.
const IDEMPOTENT = [/^出席/, /^請假 \d/, /^候補/, /^開$/, /^關$/, /^男$/, /^女$/];

/** Everything about the page that a working button could change. The
 * hash covers the whole document: comparing a prefix missed a tab panel
 * toggling `hidden` and a month of the calendar redrawing, and reported
 * working controls as dead. Defined inline rather than as a global so it
 * survives the navigation some of these buttons cause.
 *
 * Requests are counted outside the page, not from performance timings:
 * that buffer holds 250 entries and stops recording once full, which on
 * a page with a lot of controls quietly turns every later button into a
 * dead one. */
const SNAPSHOT = `(() => {
  const s = document.body.innerHTML;
  let h = 5381;
  for (let i = 0; i < s.length; i += 1) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;
  return { html: s.length + ":" + h, url: location.href };
})()`;

/** Waits for the page to be worth auditing.
 *
 * This, not `goto`'s own wait condition, is what decides the page is
 * ready — the pages are loaded with `domcontentloaded` on purpose.
 * Playwright's `networkidle` looked stricter and was simply flaky: it
 * failed the run outright on two different pages on two different days,
 * having found nothing wrong with either, because a page that is
 * perfectly usable can keep a connection busy past thirty seconds.
 *
 * What replaces it has to be at least as strong, and the first attempt
 * was not: "more than three buttons, unchanged for a moment" audited 4
 * controls on the money screen instead of 24, and passed. These pages
 * build in waves — identity, then clubs, then the season, then the
 * roster — and between two waves the button count sits perfectly still
 * while a request is in flight. So both conditions have to hold: nothing
 * outstanding to this project's own API, and the controls have stopped
 * appearing. */
async function settle(page, traffic) {
  let stable = 0;
  let last = -1;
  for (let waited = 0; waited < 25000 && stable < 3; waited += 250) {
    const now = await page
      .evaluate(() => document.querySelectorAll("button").length)
      .catch(() => last);
    const quiet = !traffic || traffic.inFlight === 0;
    stable = quiet && now === last && now > 3 ? stable + 1 : 0;
    last = now;
    await page.waitForTimeout(250);
  }
  await page.waitForTimeout(600);
}

async function pressEach(page, path, { only, dialogs }) {
  const sel = only ? `${only} button` : "button";
  const total = await page.evaluate((s) => document.querySelectorAll(s).length, sel);

  const dead = [];
  let pressed = 0;
  for (let i = 0; i < total; i += 1) {
    // Every press in this pass starts from the same screen: sheets shut.
    // Without it, a press that redraws the page shifts the indices, the
    // same control comes round again at a later one, and opening a sheet
    // that is already open is correctly nothing — which read as a dead
    // 看名單 button and cost an investigation to prove otherwise.
    //
    // Before the button is measured, not after: a control inside a sheet
    // this just shut has no height, so it is skipped as unreachable
    // rather than pressed into a closed sheet and reported dead — which
    // is what every ✕ did when this sat below. The second pass
    // deliberately does none of it: its whole subject is the controls
    // inside the open sheet.
    if (!only) {
      await page.evaluate(() => {
        document.querySelectorAll(".gsheet-backdrop").forEach((el) => {
          el.hidden = true;
        });
      });
    }

    // Re-read the list every time rather than holding handles or tags.
    // Almost every click here redraws part of the page, which detaches
    // whatever was noted earlier — an earlier version tagged the buttons
    // once and then reported perfectly good controls as dead because the
    // tag, not the button, had gone.
    const info = await page.evaluate(
      ([s, n]) => {
        const b = document.querySelectorAll(s)[n];
        if (!b) return null;
        const r = b.getBoundingClientRect();
        return {
          text: (b.textContent || "").trim().slice(0, 20),
          usable: r.height > 0 && r.width > 0 && !b.disabled,
        };
      },
      [sel, i]
    );
    if (!info || !info.usable) continue;
    if (SKIP.some((re) => re.test(info.text))) continue;

    const before = await page.evaluate(SNAPSHOT);
    const dialogsBefore = dialogs.count;
    const netBefore = dialogs.requests;
    await page
      .evaluate(
        ([s, n]) => {
          const b = document.querySelectorAll(s)[n];
          if (b) b.click();
        },
        [sel, i]
      )
      .catch(() => {});
    await page.waitForTimeout(900);
    const after = await page.evaluate(SNAPSHOT).catch(() => ({ html: "gone", url: "gone" }));

    pressed += 1;
    const acted =
      before.html !== after.html ||
      before.url !== after.url ||
      dialogs.requests > netBefore ||
      dialogs.count > dialogsBefore;
    if (!acted && !IDEMPOTENT.some((re) => re.test(info.text))) {
      dead.push(info.text || "(no label)");
    }

    if (before.url !== after.url) {
      await page.goto(`${BASE}/${path}?as=${AS}`, { waitUntil: "domcontentloaded" });
      await settle(page, dialogs);
    }
  }
  return { pressed, dead };
}

async function auditPage(browser, path) {
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  const errors = [];
  const notes = [];
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 120)}`));
  // The console line for a failed request says only "Failed to load
  // resource"; the response handler below has the status and the URL, so
  // that one is left to it rather than reported twice with less detail.
  page.on("console", (m) => {
    if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) {
      errors.push(`console: ${m.text().slice(0, 120)}`);
    }
  });
  // A 4xx is the server refusing something, which is often the right
  // answer to what this tool just did — it presses 移出球隊 on the
  // organizer, and 移除 twice on the same row. Those are worth reading
  // but they are not failures, so they are listed without failing the
  // run. A 5xx or a JS exception always is.
  page.on("response", async (r) => {
    if (!r.url().includes(":8000") || r.status() < 400) return;
    const line = `${r.status()} ${r.request().method()} ${r.url().replace(/^https?:\/\/[^/]+/, "")}`;
    if (r.status() >= 500) {
      const body = await r.text().catch(() => "?");
      errors.push(line + " -- " + body.slice(0, 300));
    } else {
      notes.push(line);
    }
  });
  const dialogs = { count: 0, requests: 0, inFlight: 0 };
  page.on("dialog", (d) => {
    dialogs.count += 1;
    d.accept("1");
  });
  page.on("request", (r) => {
    if (!r.url().includes(":8000")) return;
    dialogs.requests += 1;
    dialogs.inFlight += 1;
  });
  const finished = (r) => {
    if (r.url().includes(":8000")) dialogs.inFlight = Math.max(0, dialogs.inFlight - 1);
  };
  page.on("requestfinished", finished);
  page.on("requestfailed", finished);

  await page.goto(`${BASE}/${path}?as=${AS}`, { waitUntil: "domcontentloaded" });
  await settle(page, dialogs);

  // Pass one: the page as it opens.
  const first = await pressEach(page, path, { dialogs });

  // Pass two: inside the game sheet, which the first pass will have
  // opened if this page has one. Its controls are the ones the tab bug
  // killed, so they are the point of the exercise.
  let second = { pressed: 0, dead: [] };
  const hasSheet = await page.evaluate(() => {
    const b = document.getElementById("game-sheet-backdrop");
    if (!b) return false;
    if (b.hidden && typeof openGameSheet === "function") openGameSheet();
    return true;
  });
  if (hasSheet) {
    await page.waitForTimeout(600);
    second = await pressEach(page, path, { only: "#game-detail", dialogs });
  }

  await page.close();
  return {
    path,
    pressed: first.pressed + second.pressed,
    dead: [...new Set([...first.dead, ...second.dead])],
    errors: [...new Set(errors)],
    notes: [...new Set(notes)],
  };
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  let problems = 0;
  for (const path of PAGES) {
    const r = await auditPage(browser, path).catch((e) => ({
      path,
      pressed: 0,
      dead: [],
      errors: [`audit crashed: ${String(e).slice(0, 160)}`],
      notes: [],
    }));
    const bad = r.dead.length || r.errors.length;
    console.log(`${bad ? "FAIL" : "ok  "} ${r.path} — 按了 ${r.pressed} 個按鈕`);
    if (r.dead.length) console.log(`       沒反應: ${r.dead.join(", ")}`);
    for (const e of r.errors) console.log(`       ${e}`);
    for (const n of r.notes) console.log(`       （伺服器拒絕，已回報給使用者）${n}`);
    if (bad) problems += 1;
  }
  await browser.close();
  console.log(
    problems ? `\n${problems} 個頁面有問題。` : "\n每一頁的每個按鈕都有反應，沒有 console 錯誤。"
  );
  process.exit(problems ? 1 : 0);
})();
