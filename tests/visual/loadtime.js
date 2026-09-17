// How long each page takes to put something real on screen.
//
// Reported twice, about two different pages: 「代辦事項那個資訊卡會進入網頁
// 後過很久才載入出現」 and 「個人資料頁面的我的球隊，每次都會進入網頁後過很久
// 才載入出現」, followed by 「我希望你要去檢查每個物件功能都不要出現這樣的問題
// ⋯⋯不能慢」. One page at a time is how this gets missed, so every page is
// measured here and the slow ones have to answer for themselves.
//
// Cold vs warm is the whole point of the measurement, not a detail.
// getJsonSWR keeps the last answer in localStorage and paints it before
// the network replies, so a page wired through it shows content almost
// immediately on a second visit while a page using a bare fetch() shows
// nothing until the server answers — and the two are indistinguishable
// if you only ever measure one of them. Cold runs in a brand-new browser
// context with empty storage; warm reuses it, so the cache is primed by
// the cold run that just happened.
//
// Budgets rather than a printout: a check that only prints numbers stops
// being read. These are deliberately loose — they are here to catch a
// page falling off a cliff, not to police tens of milliseconds.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const WHO = "周恆";
const CLUB = "測試球隊（seed）";

const COLD_BUDGET = 4000;
const WARM_BUDGET = 1500;

const auth = {
  Authorization: `Bearer dev:${encodeURIComponent(WHO)}`,
  "Content-Type": "application/json",
};

// Each page's "there is something real on screen" test, taken from the
// line in that page's own source that reveals its content — not guessed
// from the markup. A proxy that a cached copy satisfies is correct here:
// the cached copy *is* content, and showing it fast is the point.
const PAGES = [
  {
    file: "member.html",
    what: "會員頁・月曆",
    // render() unhides #month-cal — member.html:690.
    ready: `!!document.querySelector("#month-cal:not([hidden])")`,
  },
  {
    file: "organizer.html",
    what: "總覽・主卡",
    // showPageLoading fills #hero-wrap; render/renderNoSeason replace it.
    ready: `(() => { const el = document.getElementById("hero-wrap");
      return !!el && !el.querySelector(".spinner") && el.innerHTML.trim().length > 0; })()`,
  },
  {
    file: "organizer-ledger.html",
    what: "帳務・分頁",
    // setHasSeason(true) unhides the tabs — organizer-ledger.html:190.
    ready: `!!document.querySelector("#ledger-tabs:not([hidden])")`,
  },
  {
    file: "organizer-members.html",
    what: "名單・名冊",
    // loadMembers unhides #roster-section — organizer-members.html:297.
    ready: `!!document.querySelector("#roster-section:not([hidden])")`,
  },
  {
    file: "organizer-settings.html",
    what: "設定・表單",
    ready: `!!document.querySelector("#settings-form:not([hidden])")`,
  },
  {
    file: "profile.html",
    what: "個人資料・我的球隊（標題）",
    // renderClubs sets this after its first hop — profile.html:271.
    ready: `/我的球隊 \\d+ 個/.test((document.getElementById("clubs-label") || {}).textContent || "")`,
  },
  {
    file: "profile.html",
    what: "個人資料・我的球隊（明細）",
    // ...and the rows only after N more ledger reads — profile.html:281.
    // Measured separately because the gap between these two marks is
    // exactly the wait that was reported.
    ready: `((document.getElementById("clubs-list") || {}).innerHTML || "").trim().length > 0`,
  },
  {
    file: "developer.html",
    what: "後台總覽",
    // Resolves on the permission empty state too, which is fine: the
    // round trip is what is being timed, not the verdict.
    ready: `((document.getElementById("overview") || {}).innerHTML || "").trim().length > 0`,
  },
  {
    file: "reports.html",
    what: "問題回報",
    ready: `(() => { const el = document.getElementById("reports");
      return !!el && !el.querySelector(".spinner") && el.innerHTML.trim().length > 0; })()`,
  },
];

let failed = 0;
function report(name, problems) {
  console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
  for (const p of problems) console.log(`       ${p}`);
  if (problems.length) failed += 1;
}

/** Loads the page and returns ms until `ready` first holds, or null if
 * it never did. Timed from just before the navigation, which is the
 * moment the person tapped. */
async function timeTo(context, clubId, page_, spec) {
  const started = Date.now();
  await page_.goto(`${BASE}/${spec.file}?as=${encodeURIComponent(WHO)}`, {
    waitUntil: "commit",
  });
  try {
    await page_.waitForFunction(spec.ready, null, { timeout: 30000 });
    return Date.now() - started;
  } catch (e) {
    return null;
  }
}

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  const club = Array.isArray(clubs) ? clubs.find((c) => c.name === CLUB) : null;
  if (!club) throw new Error("run seed_dev.py first");

  const browser = await chromium.launch({ channel: "msedge" });
  const rows = [];

  try {
    for (const spec of PAGES) {
      // A new context each time is what makes "cold" mean cold: no
      // localStorage at all, so getJsonSWR has nothing to paint from.
      const context = await browser.newContext({ viewport: { width: 390, height: 900 } });
      // The remembered club is not the response cache — setting it keeps
      // the two runs comparable without warming anything being measured.
      await context.addInitScript((id) => localStorage.setItem("vf_club", String(id)), club.id);
      const page_ = await context.newPage();

      const cold = await timeTo(context, club.id, page_, spec);
      // Same context, so the cold run's answers are now in localStorage.
      const warm = await timeTo(context, club.id, page_, spec);

      rows.push({ what: spec.what, cold, warm });
      await context.close();
    }
  } finally {
    await browser.close();
  }

  const ms = (v) => (v === null ? "沒出現" : `${v}ms`);
  console.log("");
  for (const r of rows) {
    console.log(`  ${r.what.padEnd(28)} 冷 ${ms(r.cold).padStart(8)}   熱 ${ms(r.warm).padStart(8)}`);
  }
  console.log("");

  for (const r of rows) {
    report(r.what, [
      ...(r.cold === null ? ["冷開完全沒有內容出現"] : []),
      ...(r.warm === null ? ["熱開完全沒有內容出現"] : []),
      ...(r.cold !== null && r.cold > COLD_BUDGET ? [`冷開 ${r.cold}ms，超過 ${COLD_BUDGET}ms`] : []),
      ...(r.warm !== null && r.warm > WARM_BUDGET ? [`熱開 ${r.warm}ms，超過 ${WARM_BUDGET}ms`] : []),
    ]);
  }

  console.log(failed ? `\n${failed} 個畫面太慢。` : "\n每個畫面都在預算之內。");
  process.exit(failed ? 1 : 0);
})();
