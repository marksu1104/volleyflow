// 結算 and 復原結算, from the ledger page.
//
// Settling locks a season and credits every absence refund. A mis-tap
// has to be undoable, and undoing it must say plainly what it cannot
// put right: cash already handed over. See routes/seasons.py
// unsettle_season.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const WHO = "周恆";
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

const post = async (path, body) =>
  (await fetch(`${API}${path}`, { method: "POST", headers: auth, body: JSON.stringify(body || {}) })).json();
const get = async (path) => (await fetch(`${API}${path}`, { headers: auth })).json();

(async () => {
  // A club of its own: settling the seeded season would lock it for
  // every other check that uses it.
  const club = await post("/clubs", { name: "結算測試" });
  const season = await post(`/clubs/${club.id}/seasons`, {
    total_venue_cost: "1000",
    game_dates: ["2031-05-06", "2031-05-13"],
    member_names: ["阿季", "小結"],
    capacity: 2,
    minimum_roster: 1,
  });
  const gameId = season.games[0].id;
  await post("/absences", { player_name: "阿季", game_id: gameId });
  await post("/drop-ins", { player_name: "臨打甲", game_id: gameId });
  const members = (await get(`/seasons/${season.id}`)).members;
  const away = members.find((m) => m.name === "阿季");

  const errors = [];
  const browser = await chromium.launch({ channel: "msedge" });
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
  page.on("dialog", (d) => d.accept());
  await page.addInitScript((ids) => {
    localStorage.setItem("vf_club", String(ids.club));
    localStorage.setItem("vf_org_season", String(ids.season));
  }, { club: club.id, season: season.id });

  try {
    const logged = [];
    page.on("console", (m) => {
      if (m.type() === "error" || m.type() === "warning") {
        logged.push(`${m.type()}: ${m.text().slice(0, 140)}`);
      }
    });
    await page.goto(`${BASE}/organizer-ledger.html?as=${encodeURIComponent(WHO)}#settle`);
    await page.waitForSelector("#tab-closing:not([hidden])", { timeout: 20000 });
    // Ready means the button is on screen and the page knows which
    // season it is looking at — not a fixed wait that may end early.
    await page.waitForSelector("#settle-btn:visible", { timeout: 20000 });
    await page.waitForFunction(
      (id) => typeof currentSeason !== "undefined" && currentSeason && String(currentSeason.id) === String(id),
      String(season.id),
      { timeout: 20000 }
    ).catch(() => {});
    await page.click("#settle-btn");
    await page.waitForTimeout(2500);
    const settled = await page.evaluate(() => ({
      beforeHidden: document.getElementById("closing-before").hidden,
      afterShown: !document.getElementById("closing-after").hidden,
      undo: !!document.getElementById("unsettle-btn"),
      nextSeason: document.getElementById("closing-after").textContent.includes("開新一季"),
    }));
    const onServer = await get(`/seasons/${season.id}`);
    report("結算之後，畫面上同時給得出「復原」和「開新一季」", [
      ...(settled.beforeHidden && settled.afterShown ? [] : ["畫面沒有切到已結算的狀態"]),
      ...(settled.undo ? [] : ["沒有復原結算的按鈕"]),
      ...(settled.nextSeason ? [] : ["沒有開新一季的去處"]),
      ...(onServer.settled_at ? [] : ["伺服器上沒有記成已結算"]),
      // Collected and then ignored is how two hours went missing today:
      // a check that sees the error and says nothing is worse than one
      // that never looked.
      ...logged,
    ]);

    const refunded = (await get(`/clubs/${club.id}/players/${away.id}/ledger`)).balance;

    await page.click("#unsettle-btn");
    await page.waitForTimeout(2500);
    const back = await page.evaluate(() => ({
      beforeShown: !document.getElementById("closing-before").hidden,
      afterHidden: document.getElementById("closing-after").hidden,
      toast: (document.querySelector(".toast") || {}).textContent || "",
    }));
    const afterUndo = await get(`/seasons/${season.id}`);
    const ledger = await get(`/clubs/${club.id}/players/${away.id}/ledger`);
    const refunds = ledger.entries.filter((e) => e.entry_type === "absence_refund");
    report("復原之後季別解鎖，退費被沖銷掉但紀錄留著", [
      ...(afterUndo.settled_at === null ? [] : ["伺服器上還是鎖著"]),
      ...(back.beforeShown && back.afterHidden ? [] : ["畫面沒有回到未結算的樣子"]),
      ...(refunds.length === 2 ? [] : [`退費紀錄有 ${refunds.length} 筆，應該是原本那筆加一筆沖銷`]),
      ...(refunds.reduce((sum, e) => sum + Number(e.amount), 0) === 0 ? [] : ["兩筆加起來不是零"]),
      ...(back.toast.includes("已復原") ? [] : [`畫面說的是「${back.toast}」`]),
      ...errors,
    ]);

    // Now the case undoing cannot put right: the refund was handed over
    // in cash before the organizer changed their mind.
    await page.click("#settle-btn");
    await page.waitForTimeout(2000);
    // Her refund, not her balance: the season fee is charged when she
    // joins the roster and is the larger of the two, so her balance
    // after settling is still what she owes.
    const entries = (await get(`/clubs/${club.id}/players/${away.id}/ledger`)).entries;
    const refund = entries
      .filter((e) => e.entry_type === "absence_refund" && Number(e.amount) > 0)
      .reduce((most, e) => (Number(e.amount) > Number(most.amount) ? e : most)).amount;
    await post(`/clubs/${club.id}/players/${away.id}/payments`, {
      amount: `-${refund}`,
      season_id: season.id,
      note: "cash",
    });
    await page.reload();
    await page.waitForSelector("#tab-closing:not([hidden])", { timeout: 20000 });
    await page.waitForTimeout(1200);
    await page.click("#unsettle-btn");
    await page.waitForTimeout(2500);
    const told = await page.evaluate(() => (document.querySelector(".toast") || {}).textContent || "");
    report("已經領過現金的人會被指名說出來", [
      ...(told.includes("阿季") ? [] : [`畫面說的是「${told}」`]),
      ...(told.includes("應收") ? [] : ["沒有說清楚那筆現在記在誰頭上"]),
    ]);
  } finally {
    await browser.close();
    await fetch(`${API}/clubs/${club.id}`, { method: "DELETE", headers: auth });
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n結算與復原都正常。");
  process.exit(failed ? 1 : 0);
})();
