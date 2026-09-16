// An organizer's day: open 總覽, work the 待辦 line down, approve who is
// waiting, find somebody to cover a night nobody is covering, take the
// season fee — and undo the one that was taken by mistake.
//
// The point is the handover between screens. Each of these works on its
// own; what breaks is 總覽 sending you somewhere that no longer has the
// thing it promised.
//
// Builds its own club and deletes it afterwards.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const ORGANIZER = "周恆";
const MEMBER = "主揪測員";
const NEWCOMER = "主揪測新人";
const SUB = "主揪測代打";

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

const identify = async (name) =>
  (
    await fetch(`${API}/players/identify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: `dev:${encodeURIComponent(name)}`, display_name: name }),
    })
  ).json();

function inDays(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

(async () => {
  const member = await identify(MEMBER);
  const newcomer = await identify(NEWCOMER);

  const club = await post("/clubs", { name: "主揪測試" });
  const invite = await get(`/clubs/${club.id}/invite`);
  const asPlayer = (name) => ({
    Authorization: `Bearer dev:${encodeURIComponent(name)}`,
    "Content-Type": "application/json",
  });

  await post(`/clubs/${club.id}/join`, { invite: invite.token, wants_fixed_membership: true }, asPlayer(MEMBER));
  await post(`/clubs/${club.id}/members/${member.id}/approve`, { as_fixed: true });
  // Left waiting on purpose: this is what 待辦 has to notice.
  await post(`/clubs/${club.id}/join`, { invite: invite.token, wants_fixed_membership: false }, asPlayer(NEWCOMER));

  const season = await post(`/clubs/${club.id}/seasons`, {
    total_venue_cost: "900",
    game_dates: [inDays(7)],
    member_names: [ORGANIZER, MEMBER],
    capacity: 4,
    minimum_roster: 1,
  });
  const game = season.games[0];
  // A night with nobody covering it — the other thing 待辦 has to say.
  await post("/absences", { player_name: MEMBER, game_id: game.id });

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
    await page.goto(`${BASE}/organizer.html?as=${encodeURIComponent(ORGANIZER)}`);
    await page.waitForSelector("#todo-line:not([hidden])", { timeout: 25000 });
    const todo = await page.evaluate(() => ({
      main: document.getElementById("todo-main").textContent,
      more: document.getElementById("todo-more").textContent,
    }));
    await page.click("#todo-line");
    await page.waitForSelector("#todo-backdrop:not([hidden])", { timeout: 10000 });
    const sheet = await page.evaluate(() => ({
      rows: document.querySelectorAll("#todo-list .todo-row").length,
      text: document.getElementById("todo-list").innerText.replace(/\s+/g, " ").trim().slice(0, 120),
    }));
    report("總覽的待辦說得出今天要處理什麼", [
      ...(todo.main.includes("等待核准") || sheet.text.includes("等待核准")
        ? []
        : [`待辦寫的是: ${todo.main} / ${sheet.text}`]),
      ...(sheet.text.includes("沒人代打") ? [] : ["沒有提到沒人代打的那一場"]),
      ...(sheet.rows >= 2 ? [] : [`清單只有 ${sheet.rows} 列`]),
    ]);

    // 核准 sends the organizer to the roster page, which must have the
    // person it promised.
    await page.click("#todo-list [data-todo='0']");
    await page.waitForURL(/organizer-members/, { timeout: 20000 });
    // The waiting list arrives after the page does. Waiting for the
    // section, then scoping the click to that person's row, is how
    // join.js does it — a bare text= can land on a button whose row has
    // not been filled in yet, and a click that does nothing looks
    // exactly like a server that refused.
    await page.waitForSelector("#requests-section:not([hidden])", { timeout: 20000 });
    const waitingShown = await page.evaluate(() => document.body.innerText.includes("待核准"));

    const approvals = [];
    page.on("response", async (r) => {
      if (!r.url().includes("/approve")) return;
      approvals.push(`${r.status()} ${(await r.text().catch(() => "")).slice(0, 80)}`);
    });

    const waitingRow = page.locator("#requests-list .pool-row", { hasText: NEWCOMER });
    await waitingRow.locator("button", { hasText: "核准臨打" }).click();
    // Asked of the person themselves, the way join.js does it: a
    // membership is pending or active, and the club's roster is not
    // where that shows.
    let status = "none";
    for (let i = 0; i < 16; i += 1) {
      const clubs = await (
        await fetch(`${API}/players/${newcomer.id}/clubs`, { headers: asPlayer(NEWCOMER) })
      ).json();
      const mine = Array.isArray(clubs) ? clubs.find((c) => c.id === club.id) : null;
      status = mine ? mine.status : "none";
      if (status === "active") break;
      await page.waitForTimeout(400);
    }
    report("待辦把人帶到名單頁，核准之後他就進來了", [
      ...(waitingShown ? [] : ["名單頁上沒有待核准的區塊"]),
      ...(status === "active" ? [] : [`${NEWCOMER} 的會籍還是 ${status}`]),
      // The evidence, not just the verdict.
      ...(status === "active" ? [] : [`核准的回應: ${approvals.join(" | ") || "（一個請求都沒送出）"}`]),
    ]);

    // Somebody to cover the night nobody is covering.
    await page.goto(`${BASE}/organizer.html?as=${encodeURIComponent(ORGANIZER)}`);
    await page.waitForFunction(() => typeof currentSeason !== "undefined" && currentSeason, null, {
      timeout: 25000,
    });
    await page.click("#hero-wrap >> text=名單與請假");
    await page.waitForSelector("#game-sheet-backdrop:not([hidden])", { timeout: 10000 });
    await page.click('#game-sheet-backdrop button[data-gd-tab="absent"]');
    await page.waitForSelector('#game-sheet-backdrop [data-gd-panel="absent"]:not([hidden])', {
      timeout: 10000,
    });
    const absenceId = (await get(`/seasons/${season.id}`)).games[0].absences[0].id;
    await page.click(`[data-toggle-sub="${absenceId}"]`);
    await page.waitForSelector(`[data-sub-name="${absenceId}"]`, { timeout: 10000 });
    await page.fill(`[data-sub-name="${absenceId}"]`, SUB);
    await page.click(`[data-confirm-sub="${absenceId}"]`);
    await page.waitForTimeout(2500);
    const covered = (await get(`/seasons/${season.id}`)).games[0].absences.find((a) => a.id === absenceId);
    report("主揪可以替沒人代打的那一場指定人選", [
      ...(covered && covered.filled_by === SUB ? [] : [`伺服器上記到的代打是 ${covered && covered.filled_by}`]),
    ]);

    // The money, and the tap that shouldn't have happened.
    await page.goto(`${BASE}/organizer-ledger.html?as=${encodeURIComponent(ORGANIZER)}`);
    await page.waitForSelector("#ledger-tabs:not([hidden])", { timeout: 25000 });
    await page.waitForTimeout(1500);
    const collected = await page.evaluate((who) => {
      const row = [...document.querySelectorAll("#fee-list .m-row")].find((r) => r.innerText.includes(who));
      if (!row || !row.querySelector(".paid")) return false;
      row.querySelector(".paid").click();
      return true;
    }, MEMBER);
    await page.waitForTimeout(2500);
    const afterPaying = await get(`/clubs/${club.id}/players/${member.id}/ledger`);
    const payments = afterPaying.entries.filter((e) => e.entry_type === "payment");
    report("季費收得起來", [
      ...(collected ? [] : [`季費那一頁找不到 ${MEMBER} 的「已收」`]),
      ...(payments.length === 1 ? [] : [`收款紀錄有 ${payments.length} 筆`]),
    ]);

    await page.evaluate((id) => openHistory(id), member.id);
    await page.waitForSelector("#history-backdrop:not([hidden])", { timeout: 10000 });
    await page.click("#history-list >> text=復原");
    await page.waitForTimeout(2500);
    const afterUndo = await get(`/clubs/${club.id}/players/${member.id}/ledger`);
    const money = afterUndo.entries.filter((e) => e.entry_type === "payment");
    report("誤收的款可以復原，原本那筆留著", [
      ...(money.length === 2 ? [] : [`收款紀錄有 ${money.length} 筆，應該是原本那筆加一筆沖銷`]),
      ...(money.reduce((sum, e) => sum + Number(e.amount), 0) === 0 ? [] : ["兩筆加起來不是零"]),
      ...(Number(afterUndo.balance) === Number(afterPaying.balance) - Number(payments[0].amount)
        ? []
        : ["餘額沒有回到收款之前"]),
      ...errors,
    ]);
  } finally {
    await browser.close();
    await fetch(`${API}/clubs/${club.id}`, { method: "DELETE", headers: asOrganizer });
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n主揪的一天走起來都正常。");
  process.exit(failed ? 1 : 0);
})();
