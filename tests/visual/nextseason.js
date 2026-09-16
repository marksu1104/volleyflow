// 開新一季, walked the way an organizer walks it.
const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const ORGANIZER = "周恆";
const CLUB = "測試球隊（seed）";
const auth = { Authorization: `Bearer dev:${encodeURIComponent(ORGANIZER)}` };

let failed = 0;
function report(name, problems) {
  console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
  for (const p of problems) console.log(`       ${p}`);
  if (problems.length) failed += 1;
}

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  const club = clubs.find((c) => c.name === CLUB);
  if (!club) throw new Error("run seed_dev.py first");
  const seasons = await (await fetch(`${API}/clubs/${club.id}/seasons`, { headers: auth })).json();
  const current = await (await fetch(`${API}/seasons/${seasons[0].id}`, { headers: auth })).json();
  const lastDate = current.games.map((g) => g.date).sort().pop();

  const errors = [];
  const browser = await chromium.launch({ channel: "msedge" });
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 160)}`));
  page.on("dialog", (d) => d.accept());
  await page.addInitScript((id) => localStorage.setItem("vf_club", String(id)), club.id);
  await page.addInitScript((id) => localStorage.setItem("vf_org_season", String(id)), seasons[0].id);
  await page.goto(`${BASE}/organizer-settings.html?as=${encodeURIComponent(ORGANIZER)}`);
  await page.waitForSelector("#settings-form:not([hidden])", { timeout: 20000 });

  // 1. 設定 — carried over from the season being continued.
  await page.click("text=開新一季");
  await page.waitForSelector("#create-season-form:not([hidden])", { timeout: 10000 });
  const step1 = await page.evaluate(() => ({
    steps: [...document.querySelectorAll(".stepper .step")].map((s) => s.textContent.trim()),
    cost: document.getElementById("c-cost").value,
    capacity: document.getElementById("c-capacity").value,
    title: document.getElementById("create-season-title").textContent,
  }));
  report("第 1 步沿用上一季的設定", [
    ...(step1.steps.length === 4 ? [] : [`步驟有 ${step1.steps.length} 個: ${step1.steps.join(" / ")}`]),
    ...(step1.steps.join("") === "1設定2場次3名單4確認" ? [] : [`步驟名稱: ${step1.steps.join(" / ")}`]),
    ...(Number(step1.cost) > 0 ? [] : [`場地費沒有帶過來: "${step1.cost}"`]),
    ...(step1.title.includes("開新一季") ? [] : [`標題是 ${step1.title}`]),
  ]);

  // 2. 場次 — dates already run on from the last game.
  await page.click('[data-go="2"]');
  await page.waitForTimeout(300);
  const step2 = await page.evaluate(() => ({
    chips: document.querySelectorAll("#date-chips .chip:not(.add)").length,
    summary: document.getElementById("gen-summary").textContent.replace(/\s+/g, " ").trim(),
    cooled: document.querySelectorAll("#date-chips .chip-ac.on").length,
    first: (typeof wizardDates !== "undefined" && wizardDates[0]) || "",
  }));
  report("第 2 步的日期接在上一季最後一場之後", [
    ...(step2.chips > 0 ? [] : ["沒有產生任何場次"]),
    ...(step2.first && step2.first > lastDate ? [] : [`第一場 ${step2.first} 不在 ${lastDate} 之後`]),
    ...(step2.summary.includes("每人每場") ? [] : [`摘要沒有金額: ${step2.summary}`]),
    ...(step2.cooled === (current.games.every((g) => g.air_conditioned) ? step2.chips : 0)
      ? []
      : [`冷氣場次 ${step2.cooled}，上一季 ${current.games.filter((g) => g.air_conditioned).length}/${current.games.length}`]),
  ]);

  // 3. 名單 — one line on the page, the people in a sheet.
  await page.click('[data-go="3"]');
  await page.waitForTimeout(900);
  const step3 = await page.evaluate(() => ({
    summary: document.getElementById("roster-summary").textContent,
    listVisible: document.getElementById("roster-list").getBoundingClientRect().height > 0,
    sheetHidden: document.getElementById("roster-backdrop").hidden,
    panelHeight: Math.round(document.querySelector('[data-panel="3"]').getBoundingClientRect().height),
  }));
  await page.click("#roster-summary");
  await page.waitForTimeout(400);
  const sheet = await page.evaluate(() => ({
    open: !document.getElementById("roster-backdrop").hidden,
    rows: document.querySelectorAll("#roster-list .pick-row").length,
    ticked: document.querySelectorAll("#roster-list .pick-row.on").length,
    withMoney: document.querySelectorAll("#roster-list .pk-bal").length,
  }));
  report("第 3 步是一行字，人在面板裡", [
    ...(step3.summary.includes("人") ? [] : [`摘要是: ${step3.summary}`]),
    ...(step3.listVisible ? ["名單直接攤在頁面上"] : []),
    ...(step3.panelHeight <= 320 ? [] : [`這一步高 ${step3.panelHeight}px`]),
    ...(sheet.open && sheet.rows >= 5 ? [] : [`面板: ${JSON.stringify(sheet)}`]),
    ...(sheet.ticked === current.members.length ? [] : [`勾起來的是 ${sheet.ticked} 人，上一季有 ${current.members.length} 人`]),
    ...(sheet.withMoney > 0 ? [] : ["沒有人顯示餘額"]),
  ]);

  // Unticking somebody who is owed money must say so.
  const owed = await page.evaluate(() => {
    const row = [...document.querySelectorAll("#roster-list .pick-row")].find((r) => r.querySelector(".pk-bal"));
    if (!row) return null;
    row.click();
    return row.querySelector(".pk-name").textContent;
  });
  await page.click("#roster-backdrop .btn-primary");
  await page.waitForTimeout(300);
  const alert = await page.evaluate(() => ({
    shown: !document.getElementById("roster-alert").hidden,
    text: document.getElementById("roster-alert-text").textContent,
  }));
  report("把有餘額的人拿掉會說出來", [
    ...(owed ? [] : ["找不到有餘額的人"]),
    ...(alert.shown ? [] : ["沒有出現提醒"]),
    ...(alert.text.includes("餘額") ? [] : [`提醒寫的是: ${alert.text}`]),
  ]);

  // Put them back.
  await page.click("#roster-summary");
  await page.waitForTimeout(300);
  await page.evaluate(() => {
    const row = [...document.querySelectorAll("#roster-list .pick-row")].find((r) => r.querySelector(".pk-bal") && !r.classList.contains("on"));
    if (row) row.click();
  });
  await page.click("#roster-backdrop .btn-primary");
  await page.waitForTimeout(300);

  // 4. 確認 — what changed, and what everyone will be charged.
  await page.click('[data-go="4"]');
  await page.waitForTimeout(400);
  const step4 = await page.evaluate(() => ({
    rows: [...document.querySelectorAll("#confirm-diff .set-row")].map((r) => r.textContent.replace(/\s+/g, " ").trim()),
    preview: document.getElementById("create-preview").textContent.replace(/\s+/g, " ").trim().slice(0, 80),
  }));
  report("第 4 步說清楚會建立什麼", [
    ...(step4.rows.length >= 4 ? [] : [`只有 ${step4.rows.length} 列: ${step4.rows.join(" | ")}`]),
    ...(step4.rows.some((r) => r.includes("場地費")) ? [] : ["沒有場地費"]),
    ...(step4.rows.some((r) => r.includes("固定成員")) ? [] : ["沒有固定成員"]),
    ...(step4.rows.some((r) => r.includes("沿用上一季") || r.includes("上一季")) ? [] : ["沒有跟上一季比較"]),
    ...(step4.preview.includes("每人季費") ? [] : [`預覽是: ${step4.preview}`]),
  ]);
  console.log("       " + step4.rows.join("\n       "));

  // Build it, and land where the money is.
  await page.click("text=建立這一季");
  await page.waitForURL(/organizer-ledger/, { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(2500);
  const landed = await page.evaluate(() => ({
    url: location.pathname,
    tab: (document.querySelector("#ledger-tabs .on") || {}).textContent,
  }));
  report("建立後直接落在帳務 · 季費", [
    ...(landed.url.includes("organizer-ledger") ? [] : [`到了 ${landed.url}`]),
    ...(landed.tab === "季費" ? [] : [`分頁是 ${landed.tab}`]),
    ...errors,
  ]);

  // Take the practice season back out again.
  const after = await (await fetch(`${API}/clubs/${club.id}/seasons`, { headers: auth })).json();
  const made = after.find((s) => !seasons.some((old) => old.id === s.id));
  if (made) {
    const gone = await fetch(`${API}/seasons/${made.id}`, { method: "DELETE", headers: auth });
    console.log(`\n（清掉這次建立的季別 ${made.id}：${gone.status}）`);
  }

  await browser.close();
  console.log(failed ? `\n${failed} 項有問題。` : "\n開新一季整條流程都正常。");
  process.exit(failed ? 1 : 0);
})();
