// How long the signup sheet takes to open, and whether it talks to the
// server on the way.
//
// Asked for directly: 「我必須確定所有流程都可以立馬反應、不會卡頓、不會
// 切來切去，我怕點開報名表要跑很久」.
//
// The worry was well founded, and aimed at a change that was proposed
// and then withdrawn. The idea had been to stop fetching the roster and
// the quick-picks during page load and fetch them when the sheet opens
// instead — which would have put a round trip directly behind the tap,
// which is the complaint, not the fix.
//
// member.html already does the right thing: renderHero, renderCalendar,
// renderSelectedGameDetail and paintBalance run first, and only then are
// refreshMyGuestsQuietly() and fetchClubMembers() fired, un-awaited, each
// repainting only if it actually brought something new. So the data is
// in memory long before anybody taps.
//
// This check exists to keep it that way. The budget is the weaker half
// of it: a budget drifts, and a slow machine can miss one honestly. The
// assertion that matters is that **no request to our API happens between
// the tap and the sheet being usable** — because a sheet that goes
// nowhere near the network cannot be slow, and the day somebody moves
// those fetches back behind the tap, this is what says so.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const WHO = "周恆";
const CLUB = "測試球隊（seed）";

// Generous on purpose. Opening a sheet whose data is already in memory
// is a repaint; anything approaching this means it is waiting on
// something it should not be waiting on.
const OPEN_BUDGET = 600;

const auth = {
  Authorization: `Bearer dev:${encodeURIComponent(WHO)}`,
  "Content-Type": "application/json",
};

let failed = 0;
function report(name, problems) {
  console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
  for (const p of problems) console.log(`       ${p}`);
  if (problems.length) failed += 1;
}

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  const club = Array.isArray(clubs) ? clubs.find((c) => c.name === CLUB) : null;
  if (!club) throw new Error("run seed_dev.py first");

  const browser = await chromium.launch({ channel: "msedge" });
  const context = await browser.newContext({ viewport: { width: 390, height: 900 } });
  await context.addInitScript((id) => localStorage.setItem("vf_club", String(id)), club.id);
  const page = await context.newPage();

  const errors = [];
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text().slice(0, 140)}`);
  });

  // Every call to our API, so the ones made *after* the tap can be told
  // apart from the ones made during the load.
  const calls = [];
  page.on("request", (r) => {
    if (r.url().startsWith(API)) calls.push({ at: Date.now(), path: r.url().slice(API.length).split("?")[0] });
  });

  try {
    await page.goto(`${BASE}/member.html?as=${encodeURIComponent(WHO)}`);
    await page.waitForSelector("#month-cal:not([hidden])", { timeout: 30000 });
    // Settle: the background reads fired after first paint are what make
    // the tap instant, and a real person does not tap in the same
    // millisecond the calendar appears.
    await page.waitForTimeout(1500);

    const opener = await page.$('button[onclick^="openSignup"]');
    if (!opener) {
      report("報名表打開得夠快", ["找不到報名按鈕，這一項沒有量到"]);
    } else {
      const tappedAt = Date.now();
      await opener.click();
      await page.waitForSelector("#signup-backdrop:not([hidden])", { timeout: 15000 });
      await page.waitForSelector("#su-go", { timeout: 15000 });
      const ms = Date.now() - tappedAt;

      const afterTap = calls.filter((c) => c.at >= tappedAt);
      const usable = await page.evaluate(() => {
        const sheet = document.getElementById("signup-backdrop");
        const go = document.getElementById("su-go");
        return {
          open: !!sheet && !sheet.hidden,
          button: go ? (go.textContent || "").trim() : "",
          spinner: !!(sheet && sheet.querySelector(".spinner")),
        };
      });

      report("報名表打開得夠快", [
        ...(ms <= OPEN_BUDGET ? [] : [`打開花了 ${ms}ms，超過 ${OPEN_BUDGET}ms`]),
        ...(usable.open ? [] : ["面板沒有打開"]),
        // Either label is correct: a game with room says 確認報名, a full
        // one says 加入候補 (member.html renders 報名候補 on the opener for
        // the same reason). Asserting only the first reported working
        // code as broken the first time this ran, against a seeded game
        // that happened to be full.
        ...(/確認報名|加入候補/.test(usable.button)
          ? []
          : [`按鈕寫的是「${usable.button}」，兩種都不是`]),
        ...(usable.spinner ? ["面板裡還在轉圈，表示它在等東西"] : []),
      ]);

      report("按下去之後不必再等伺服器", [
        ...(afterTap.length === 0
          ? []
          : [`點開報名表又打了 ${afterTap.length} 支 API：${afterTap.map((c) => c.path).join(" ")}`]),
      ]);

      console.log(`\n打開耗時 ${ms}ms・載入時打了 ${calls.length} 支 API，點擊之後 ${afterTap.length} 支`);
    }

    report("沒有 console 錯誤", errors);
  } finally {
    await browser.close();
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n報名表是即時打開的，沒有等伺服器。");
  process.exit(failed ? 1 : 0);
})();
