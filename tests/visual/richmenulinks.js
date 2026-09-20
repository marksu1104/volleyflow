// Where the LINE rich menu's buttons actually land.
//
// Every menu entry opens member.html with a parameter rather than a
// path, because a liff.line.me link forwards query params to the LIFF
// endpoint while a path depends on how that endpoint is configured. That
// makes ?open= / ?pick= the contract between the menu and the app — and
// nothing else checks it: smoke and memberweek both load member.html
// bare, so the handler could rot into a dead button and every existing
// check would still pass.
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
    // 我的帳務 — the sheet must open *and* have its figures. Opening it
    // before the ledger lands leaves it on 載入中 forever, because
    // openLedger does not repaint when the answer arrives; waiting for
    // the backdrop alone would call that a pass.
    await open(page, "&open=ledger");
    await page.waitForSelector("#ledger-backdrop:not([hidden])", { timeout: 25000 });
    await page.waitForTimeout(1200);
    const ledger = await page.evaluate(() => ({
      total: (document.getElementById("lg-total") || {}).textContent || "",
      body: (document.getElementById("lg-entries") || {}).innerText || "",
      url: location.search,
    }));
    report("?open=ledger 打開帳務，而且裡面有數字", [
      ...(ledger.total.trim() ? [] : ["帳務面板打開了，但金額是空的"]),
      ...(ledger.body.includes("載入中") ? ["面板卡在「載入中」"] : []),
      ...(ledger.url.includes("open=") ? [`網址還留著參數：${ledger.url}`] : []),
      ...(ledger.url.includes("as=") ? [] : ["?as= 被一起清掉了，本機身分會掉"]),
    ]);

    // Reloading must not reopen it. The parameter is stripped for exactly
    // this reason — otherwise a refresh drags the reader back to a sheet
    // they closed.
    await page.reload();
    await page.waitForTimeout(2000);
    const afterReload = await page.evaluate(
      () => document.getElementById("ledger-backdrop").hidden
    );
    report("重新整理不會又把帳務面板打開", [
      ...(afterReload ? [] : ["重新整理之後面板又自己打開了"]),
    ]);

    // 問題回報 — a different page entirely, reached by redirect.
    await open(page, "&open=report");
    await page.waitForURL(/report\.html/, { timeout: 25000 });
    await page.waitForSelector("#content:not([hidden])", { timeout: 25000 });
    const reportPage = await page.evaluate(() => ({
      textarea: !!document.getElementById("report-text"),
      button: (document.getElementById("report-btn") || {}).textContent || "",
    }));
    report("?open=report 帶到回報頁，而且表單在", [
      ...(reportPage.textarea ? [] : ["回報頁上沒有輸入框"]),
      ...(reportPage.button.includes("送出") ? [] : [`按鈕寫的是「${reportPage.button}」`]),
    ]);

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
