// A member's week, walked the way a member walks it: see the next game,
// take the night off, arrange somebody to stand in, change your mind,
// then look at what you owe.
//
// Every step is a different screen and they hand state to each other, so
// this is the one check that would notice the hero card and the game
// sheet disagreeing about whether you are playing.
//
// Builds its own club so the seeded one stays as the other checks left
// it, and deletes it afterwards.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const ORGANIZER = "周恆";
const MEMBER = "週測甲";
const SUB = "週測代打";

const asOrganizer = {
  Authorization: `Bearer dev:${encodeURIComponent(ORGANIZER)}`,
  "Content-Type": "application/json",
};

let failed = 0;
function report(name, problems) {
  console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
  for (const p of problems) console.log(`       ${p}`);
  if (problems.length) failed += 1;
}

const post = async (path, body, headers) =>
  (
    await fetch(`${API}${path}`, {
      method: "POST",
      headers: headers || asOrganizer,
      body: JSON.stringify(body || {}),
    })
  ).json();
const get = async (path) => (await fetch(`${API}${path}`, { headers: asOrganizer })).json();

/** A date this many days out, written the way the app writes one. Local
 * parts, never toISOString(): Taiwan is UTC+8, so going through UTC
 * moves the day. */
function inDays(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

(async () => {
  // The member needs a LINE identity before the season names them, or
  // the roster creates a second person with the same name.
  const member = await (
    await fetch(`${API}/players/identify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: `dev:${encodeURIComponent(MEMBER)}`, display_name: MEMBER }),
    })
  ).json();

  const club = await post("/clubs", { name: "一週測試" });
  const invite = await get(`/clubs/${club.id}/invite`);
  await post(
    `/clubs/${club.id}/join`,
    { invite: invite.token, wants_fixed_membership: true },
    { Authorization: `Bearer dev:${encodeURIComponent(MEMBER)}`, "Content-Type": "application/json" }
  );
  await post(`/clubs/${club.id}/members/${member.id}/approve`, { as_fixed: true });

  const season = await post(`/clubs/${club.id}/seasons`, {
    total_venue_cost: "1200",
    game_dates: [inDays(7), inDays(14)],
    member_names: [ORGANIZER, MEMBER],
    capacity: 4,
    minimum_roster: 1,
  });
  const game = season.games[0];

  const errors = [];
  const browser = await chromium.launch({ channel: "msedge" });
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(`console: ${m.text().slice(0, 140)}`);
  });
  page.on("dialog", (d) => d.accept());
  await page.addInitScript((id) => localStorage.setItem("vf_club", String(id)), club.id);

  try {
    await page.goto(`${BASE}/member.html?as=${encodeURIComponent(MEMBER)}`);
    await page.waitForFunction(
      () => typeof currentSeason !== "undefined" && currentSeason,
      null,
      { timeout: 20000 }
    );
    await page.waitForTimeout(600);

    const opening = await page.evaluate(() => ({
      hero: document.querySelector("#hero-wrap").innerText.replace(/\s+/g, " ").trim().slice(0, 120),
      canTakeLeave: !!document.querySelector('#hero-wrap button[onclick^="recordAbsence"]'),
    }));
    report("打開就看到下一場，而且可以請假", [
      ...(opening.canTakeLeave ? [] : ["首頁上沒有請假的按鈕"]),
      ...(opening.hero.includes("/") ? [] : [`資訊卡上看不到日期: ${opening.hero}`]),
    ]);

    // 請假
    await page.click('#hero-wrap button[onclick^="recordAbsence"]');
    await page.waitForTimeout(1800);
    const afterLeave = await page.evaluate(() => ({
      canCancel: !!document.querySelector('#hero-wrap button[onclick^="cancelAbsence"]'),
    }));
    const serverAfterLeave = await get(`/seasons/${season.id}`);
    const awayNames = serverAfterLeave.games[0].absences.map((a) => a.player_name);
    report("請假之後，畫面和伺服器都說你不來了", [
      ...(afterLeave.canCancel ? [] : ["按鈕沒有換成取消請假"]),
      ...(awayNames.includes(MEMBER) ? [] : [`伺服器上的請假名單是 ${awayNames.join("、") || "空的"}`]),
    ]);

    // 指定代打, from the game sheet. Never swallow the click that opens
    // it: a missed open leaves every later click hammering a hidden
    // element for thirty seconds and blames the wrong thing.
    await page.click("#hero-wrap .hero-more");
    await page.waitForSelector("#game-sheet-backdrop:not([hidden])", { timeout: 10000 });
    // The sheet has three tabs and 指定代打 lives in 請假; the other
    // panels are hidden, so the button exists long before it is visible.
    await page.click('#game-sheet-backdrop button[data-gd-tab="absent"]');
    await page.waitForSelector('#game-sheet-backdrop [data-gd-panel="absent"]:not([hidden])', {
      timeout: 10000,
    });
    const absenceId = serverAfterLeave.games[0].absences.find((a) => a.player_name === MEMBER).id;
    await page.click(`[data-toggle-sub="${absenceId}"]`);
    await page.waitForSelector(`[data-sub-name="${absenceId}"]`, { timeout: 10000 });
    await page.fill(`[data-sub-name="${absenceId}"]`, SUB);
    await page.click(`[data-confirm-sub="${absenceId}"]`);
    await page.waitForTimeout(2000);
    const withSub = await get(`/seasons/${season.id}`);
    const covered = withSub.games[0].absences.find((a) => a.id === absenceId);
    report("指定代打之後，那一場有人替你上場", [
      ...(covered && covered.filled_by ? [] : ["伺服器上沒有記到代打"]),
      ...(covered && covered.filled_by === SUB ? [] : [`記到的是 ${covered && covered.filled_by}`]),
    ]);

    // 取消請假 — the stand-in is released with it.
    // Shut it the way a person would, so the close button stays checked.
    await page.click("#game-sheet-backdrop .gsheet-close");
    // state: "hidden" — waitForSelector defaults to waiting for a
    // *visible* element, so asking it for `[hidden]` waits for something
    // that can never happen and blames the sheet for not closing.
    await page.waitForSelector("#game-sheet-backdrop", { state: "hidden", timeout: 10000 });
    // With somebody standing in, the card stops offering 取消請假 and
    // offers 取消代打 instead: you take the arrangement back first, then
    // the leave. Two taps rather than one that would quietly drop
    // another person's evening. This is the state a member actually
    // sees, so it is checked rather than clicked past.
    const covering = await page.evaluate(() => ({
      text: document.getElementById("hero-wrap").innerText.replace(/\s+/g, " ").trim().slice(0, 160),
      cancelsSub: !!document.querySelector('#hero-wrap button[onclick^="cancelDropIn"]'),
    }));
    report("安排好代打之後，資訊卡說得出是誰替你上場", [
      ...(covering.text.includes(SUB) ? [] : [`資訊卡寫的是: ${covering.text}`]),
      ...(covering.cancelsSub ? [] : ["沒有取消代打的按鈕"]),
    ]);

    // 取消代打, then 取消請假 — the way back is the way in, reversed.
    await page.click('#hero-wrap button[onclick^="cancelDropIn"]');
    await page.waitForSelector('#hero-wrap button[onclick^="cancelAbsence"]', { timeout: 15000 });
    await page.click('#hero-wrap button[onclick^="cancelAbsence"]');
    await page.waitForTimeout(2200);
    const back = await get(`/seasons/${season.id}`);
    const stillAway = back.games[0].absences.map((a) => a.player_name);
    const playing = back.games[0].confirmed_drop_ins.map((d) => d.player_name);
    report("收回代打再取消請假，名單回到原狀", [
      ...(stillAway.includes(MEMBER) ? ["伺服器上還記著請假"] : []),
      ...(playing.includes(SUB) ? [`${SUB} 還留在名單上`] : []),
    ]);

    // 看自己的帳
    await page.click("#balance-chip");
    await page.waitForTimeout(1200);
    const ledger = await page.evaluate(() => ({
      open: !document.getElementById("ledger-backdrop").hidden,
      total: (document.getElementById("lg-total") || {}).innerText || "",
      entries: document.querySelectorAll("#lg-entries .set-row, #lg-entries .m-row, #lg-entries > div").length,
    }));
    report("自己的帳看得到季費", [
      ...(ledger.open ? [] : ["帳務面板沒有打開"]),
      ...(ledger.total.includes("$") ? [] : [`總額看不到金額: ${ledger.total}`]),
      ...(ledger.entries > 0 ? [] : ["一筆紀錄都沒有"]),
      ...errors,
    ]);
  } finally {
    await browser.close();
    await fetch(`${API}/clubs/${club.id}`, { method: "DELETE", headers: asOrganizer });
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n成員的一週走起來都正常。");
  process.exit(failed ? 1 : 0);
})();
