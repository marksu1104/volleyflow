// Where the LINE rich menu's 場次與報名 button actually lands.
//
// This file used to check `?open=ledger` and `?open=report` as well,
// from when one LIFF app opened member.html and a parameter chose the
// destination. Since 2026-09-18 each destination has its own LIFF app,
// so those two parameters have pointed at nothing — the checks stayed
// green while guarding a path no button takes. Removed 2026-09-22, the
// same cleanup rootredirect.js got at the time and this missed.
//
// What remains is the one half of the contract a desktop browser can
// still answer: `?pick=1`, which the menu's first column carries. The
// other half — that the three liff.line.me apps land on the three right
// pages — cannot be tested from here at all and must be opened inside
// LINE (see the LIFF note in docs/HANDOFF.md §3.1).
//
// clubpicker.js covers whether the chooser appears and what choosing
// does. This covers something it does not: that arriving with ?pick=1
// opens the chooser *and nothing else* — the ledger and signup sheets
// live on the same page and are one stray branch away from popping up
// over it.
//
// A dead menu button is a failure nobody reports as a bug. They just
// stop using it.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const WHO = "周恆";
const CLUB = "測試球隊（seed）";

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

const open = (page, query) =>
  page.goto(`${BASE}/member.html?as=${encodeURIComponent(WHO)}${query}`);

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  const club = Array.isArray(clubs) ? clubs.find((c) => c.name === CLUB) : null;
  if (!club) throw new Error("run seed_dev.py first");

  const errors = [];
  const browser = await chromium.launch({ channel: "msedge" });
  const context = await browser.newContext({ viewport: { width: 390, height: 900 } });
  await context.addInitScript((id) => localStorage.setItem("vf_club", String(id)), club.id);
  const page = await context.newPage();
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text().slice(0, 140)}`);
  });

  try {
    // 場次與報名 — from the rich menu this is ?pick=1, so somebody
    // with several clubs may deliberately choose rather than being sent
    // to the one remembered on this device. The parameter is one-shot:
    // refreshing after choosing must not ask again.
    await open(page, "&pick=1");
    await page.waitForSelector("[data-pick-club]", { timeout: 25000 });
    const picker = await page.evaluate(() => ({
      count: document.querySelectorAll("[data-pick-club]").length,
      url: location.search,
      ledgerOpen: !document.getElementById("ledger-backdrop").hidden,
      signupOpen: !document.getElementById("signup-backdrop").hidden,
    }));
    report("?pick=1 顯示球隊選擇，不會彈出其他面板", [
      ...(picker.count > 1 ? [] : [`只顯示 ${picker.count} 個球隊`]),
      ...(picker.url.includes("pick=") ? [`網址還留著參數：${picker.url}`] : []),
      ...(picker.ledgerOpen ? ["帳務面板自己打開了"] : []),
      ...(picker.signupOpen ? ["報名面板自己打開了"] : []),
      ...errors,
    ]);
  } finally {
    await browser.close();
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n選單三個按鈕都落在該去的地方。");
  process.exit(failed ? 1 : 0);
})();
