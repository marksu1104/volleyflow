// How long the LINE rich menu makes somebody wait.
//
// Reported after the menu went live: 「我點擊這些網址後都還要等待跳轉的
// 時間，我覺得很不好，能不要有等待跳轉，要馬上能直接看到頁面是很重要的」.
//
// loadtime.js already measures each page opened directly. This measures
// something different and worse: what the *menu* costs, which includes
// work loadtime.js never sees.
//
//   場次與報名  ->  member.html                 (no redirect)
//   我的帳務    ->  member.html?open=ledger     (waits for the ledger,
//                                                then opens a sheet)
//   問題回報    ->  member.html?open=report     (boots member.html fully,
//                                                THEN navigates away)
//
// The third is the one that was complained about, and the cost is
// structural rather than incidental: member.html boots, verifies an
// identity, resolves the reader's clubs, and only then calls
// location.replace("report.html") — a second document load, after the
// reader has already waited through a first one they never wanted.
//
// Two caveats, both of which must be read with every number below,
// because they pull in opposite directions and neither is small.
//
// Locally the identity comes from ?as=, so the real LIFF SDK never
// loads and liff.init() never runs. On a phone both sit inside *each*
// boot counted here, which makes these figures a floor.
//
// But the identity-call counts below overstate the phone's case, and
// this check must not be read as saying otherwise: initLiffIdentity
// caches the resolved identity in sessionStorage (shared.js), so a real
// second boot skips /players/identify entirely. Dev login has no such
// cache and re-identifies every time. So on a phone the redirect costs
// a document load plus a second liff.init() — not a second identity
// round trip. The wait is real; this is what it is actually made of.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const WHO = "周恆";
const CLUB = "測試球隊（seed）";

const auth = {
  Authorization: `Bearer dev:${encodeURIComponent(WHO)}`,
  "Content-Type": "application/json",
};

// What "the reader can use what they clicked" means for each cell. Taken
// from the line in each page that actually reveals the content, the same
// way loadtime.js does it — not guessed from the markup, and not the
// backdrop alone, which appears while the sheet still says 載入中.
const CELLS = [
  {
    name: "場次與報名",
    query: "",
    // render() unhides #month-cal — member.html.
    ready: `!!document.querySelector("#month-cal:not([hidden])")`,
  },
  {
    name: "我的帳務",
    query: "&open=ledger",
    // The sheet is only useful once it carries figures; openLedger paints
    // 載入中 first and does not repaint. Same criterion richmenulinks.js
    // asserts on.
    ready: `(() => {
      const b = document.getElementById("ledger-backdrop");
      if (!b || b.hidden) return false;
      const t = document.getElementById("lg-total");
      return !!t && t.textContent.trim().length > 0;
    })()`,
  },
  {
    name: "問題回報",
    query: "&open=report",
    // report.html unhides #content once identity is verified.
    ready: `!!document.querySelector("#content:not([hidden])")`,
  },
];

async function measure(context, cell) {
  const page = await context.newPage();

  // Every call this reader's browser makes to our API, so the repeated
  // work shows up as a list rather than a claim.
  const apiCalls = [];
  // Real document loads only. An earlier version of this counted
  // page.on("framenavigated"), which also fires for history.replaceState
  // — and both the ledger and the report cell call replaceState to strip
  // ?open= from the URL. That made the ledger cell, which never leaves
  // the page, report the same "sent somewhere else" warning as the one
  // cell that genuinely does. Counting document requests cannot confuse
  // a URL rewrite with a page load.
  let documentLoads = 0;
  page.on("request", (r) => {
    if (r.resourceType() === "document") documentLoads += 1;
    if (r.url().startsWith(API)) apiCalls.push(r.url().slice(API.length).split("?")[0]);
  });

  const started = Date.now();
  await page.goto(`${BASE}/member.html?as=${encodeURIComponent(WHO)}${cell.query}`);
  await page.waitForFunction(cell.ready, null, { timeout: 30000 });
  const ms = Date.now() - started;

  // The exact path, not a substring: matching "/me" also matched
  // /clubs/{id}/members, so this used to credit the roster call as an
  // identity check and miss /players/identify entirely.
  const identityCalls = apiCalls.filter((u) => u === "/players/identify").length;
  await page.close();
  return { ms, documentLoads, requests: apiCalls.length, identityCalls, apiCalls };
}

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  const club = Array.isArray(clubs) ? clubs.find((c) => c.name === CLUB) : null;
  if (!club) throw new Error("run seed_dev.py first");

  const browser = await chromium.launch({ channel: "msedge" });
  try {
    for (const cell of CELLS) {
      // Cold: a brand-new context, empty storage, nothing cached — what
      // somebody opening the menu for the first time today gets.
      const cold = await browser.newContext({ viewport: { width: 390, height: 900 } });
      await cold.addInitScript((id) => localStorage.setItem("vf_club", String(id)), club.id);
      const first = await measure(cold, cell);
      // Warm: the same context again, so getJsonSWR's cache is primed by
      // the run that just happened.
      const second = await measure(cold, cell);
      await cold.close();

      console.log(`${cell.name}`);
      console.log(`  冷開  ${String(first.ms).padStart(5)}ms   熱開  ${String(second.ms).padStart(5)}ms`);
      console.log(`  頁面載入 ${first.documentLoads} 次   API 請求 ${first.requests} 次（身分驗證 ${first.identityCalls} 次）`);
      if (first.documentLoads > 1) {
        console.log(`  ⚠ 讀者等完一個頁面，才被送去另一個頁面`);
      }
      console.log(`  冷開時打的 API：${first.apiCalls.join(" ") || "（無）"}`);
      console.log("");
    }
  } finally {
    await browser.close();
  }

  console.log("提醒：本機用 ?as= 取得身分，沒有載入 LIFF SDK，也沒有跑 liff.init()。");
  console.log("手機上這兩件事在每一次啟動裡都會發生，所以上面每個數字都是下限。");
})();
