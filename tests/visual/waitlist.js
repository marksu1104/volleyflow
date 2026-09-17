// A guest queues for a full game, somebody drops out, the queue moves
// up by itself, and the organizer takes the money on the night.
//
// The promotion happens on the server; what this checks is that the
// screens agree with it — the member's game sheet moving a name from
// 候補 to 出席, and the ledger's 當天臨打 offering to collect from
// whoever actually played.
//
// Setup runs through the API and only the screens are driven, so a
// picker's internals can change without this check inventing a failure.
// Builds its own club and deletes it afterwards.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const ORGANIZER = "周恆";
const MEMBER = "候補測員";
const GUEST = "候補來賓";

const asOrganizer = {
  Authorization: `Bearer dev:${encodeURIComponent(ORGANIZER)}`,
  "Content-Type": "application/json",
};
const asMember = {
  Authorization: `Bearer dev:${encodeURIComponent(MEMBER)}`,
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

function inDays(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

(async () => {
  const member = await (
    await fetch(`${API}/players/identify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: `dev:${encodeURIComponent(MEMBER)}`, display_name: MEMBER }),
    })
  ).json();

  const club = await post("/clubs", { name: "候補測試" });
  const invite = await get(`/clubs/${club.id}/invite`);
  await post(`/clubs/${club.id}/join`, { invite: invite.token, wants_fixed_membership: true }, asMember);
  await post(`/clubs/${club.id}/members/${member.id}/approve`, { as_fixed: true });

  // Capacity two, two fixed members: the night is already full, so the
  // guest can only queue.
  const season = await post(`/clubs/${club.id}/seasons`, {
    total_venue_cost: "800",
    game_dates: [inDays(7)],
    member_names: [ORGANIZER, MEMBER],
    capacity: 2,
    minimum_roster: 1,
  });
  const game = season.games[0];

  const queued = await post("/drop-ins", { player_name: GUEST, game_id: game.id }, asMember);
  if (queued.status !== "waitlisted") {
    console.log(`FAIL 這一場沒有滿，${GUEST} 直接進了名單（${queued.status}）`);
    await fetch(`${API}/clubs/${club.id}`, { method: "DELETE", headers: asOrganizer });
    process.exit(1);
  }

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
    await page.waitForFunction(() => typeof currentSeason !== "undefined" && currentSeason, null, {
      timeout: 20000,
    });
    await page.click("#hero-wrap .hero-more");
    await page.waitForSelector("#game-sheet-backdrop:not([hidden])", { timeout: 10000 });
    await page.click('#game-sheet-backdrop button[data-gd-tab="queued"]');
    await page.waitForSelector('#game-sheet-backdrop [data-gd-panel="queued"]:not([hidden])', {
      timeout: 10000,
    });
    const waiting = await page.evaluate(
      () => document.querySelector('[data-gd-panel="queued"]').innerText.replace(/\s+/g, " ").trim()
    );
    report("額滿時報名的人排進候補，畫面說得出來", [
      ...(waiting.includes(GUEST) ? [] : [`候補那一頁寫的是: ${waiting.slice(0, 80)}`]),
    ]);

    // What the signup sheet quotes on a full game. $0 is a price, and it
    // reads as "this is free" on a night that charges a share a head —
    // reported from real use on 2026-09-16.
    await page.click("#game-sheet-backdrop .gsheet-close");
    await page.waitForSelector("#game-sheet-backdrop", { state: "hidden", timeout: 10000 });
    await page.evaluate((id) => openSignup(id), game.id);
    await page.waitForSelector("#signup-backdrop:not([hidden])", { timeout: 10000 });
    const quote = await page.evaluate(() => ({
      label: document.getElementById("su-total-label").textContent,
      amount: document.getElementById("su-amt").textContent,
      breakdown: document.getElementById("su-breakdown").textContent,
      go: document.getElementById("su-go").textContent,
    }));
    // This member is already playing, so the sheet does not put them in
    // the list — it opens empty, which is the state the screenshot on
    // 2026-09-16 was of. Somebody who is *not* already playing opens the
    // same sheet pre-filled with themselves, and the two looked like
    // different screens until 2026-09-17; the bar keeps one grammar in
    // every state now — 合計, a figure, and a breakdown — so the queue is
    // named underneath rather than replacing the total. What must never
    // appear is $0, which reads as "this is free" on a night that
    // charges a share a head.
    report("額滿時的報名單不會用 $0 回答", [
      ...(quote.amount.includes("$0") ? ["金額寫著 $0"] : []),
      ...(quote.go.includes("候補") ? [] : [`按鈕寫的是「${quote.go}」`]),
      ...(quote.label === "合計" ? [] : [`總計那一行寫的是「${quote.label}」`]),
      ...(quote.breakdown.includes("先新增") || quote.breakdown.includes("候補")
        ? []
        : [`寫的是「${quote.label} ${quote.amount}・${quote.breakdown}」`]),
    ]);
    await page.click("#signup-backdrop .gsheet-close");
    await page.waitForSelector("#signup-backdrop", { state: "hidden", timeout: 10000 });

    // Somebody drops out: the queue moves up without anyone asking.
    await post("/absences", { player_name: ORGANIZER, game_id: game.id });
    const afterLeave = await get(`/seasons/${season.id}`);
    const nowPlaying = afterLeave.games[0].confirmed_drop_ins.map((d) => d.player_name);
    const stillQueued = afterLeave.games[0].waitlist_entries.map((w) => w.player_name);

    await page.reload();
    // Wait for the thing being asserted, not for the page to have *a*
    // season: reads are cache-then-network, and the cached copy predates
    // the promotion — satisfying a bare "currentSeason exists" wait with
    // the very list this is here to check has changed.
    await page.waitForFunction(
      (guest) =>
        typeof currentSeason !== "undefined" &&
        currentSeason &&
        currentSeason.games[0].confirmed_drop_ins.some((d) => d.player_name === guest),
      GUEST,
      { timeout: 20000 }
    );
    await page.click("#hero-wrap .hero-more");
    await page.waitForSelector("#game-sheet-backdrop:not([hidden])", { timeout: 10000 });
    const attending = await page.evaluate(
      () => document.querySelector('[data-gd-panel="attending"]').innerText.replace(/\s+/g, " ").trim()
    );
    report("有人請假之後，候補的人自動遞補上場", [
      ...(nowPlaying.includes(GUEST) ? [] : [`伺服器上的出席名單是 ${nowPlaying.join("、") || "空的"}`]),
      ...(stillQueued.includes(GUEST) ? [`${GUEST} 還留在候補裡`] : []),
      ...(attending.includes(GUEST) ? [] : [`出席那一頁寫的是: ${attending.slice(0, 80)}`]),
    ]);

    // The night's money: the organizer collects from whoever played.
    await page.goto(`${BASE}/organizer-ledger.html?as=${encodeURIComponent(ORGANIZER)}`);
    await page.waitForSelector("#ledger-tabs:not([hidden])", { timeout: 20000 });
    await page.click('#ledger-tabs [data-tab="dropin"]');
    await page.waitForSelector("#tab-dropin:not([hidden])", { timeout: 10000 });
    await page.waitForTimeout(1200);
    const owedRow = await page.evaluate((who) => {
      const row = [...document.querySelectorAll("#dropin-list .m-row")].find((r) =>
        r.innerText.includes(who)
      );
      return row ? { text: row.innerText.replace(/\s+/g, " ").trim(), canCollect: !!row.querySelector(".paid") } : null;
    }, GUEST);
    report("當天臨打的錢，帳務頁收得到", [
      ...(owedRow ? [] : [`當天臨打那一頁找不到 ${GUEST}`]),
      ...(owedRow && owedRow.canCollect ? [] : ["那一列沒有「已收」可以按"]),
    ]);

    if (owedRow && owedRow.canCollect) {
      await page.evaluate((who) => {
        const row = [...document.querySelectorAll("#dropin-list .m-row")].find((r) =>
          r.innerText.includes(who)
        );
        row.querySelector(".paid").click();
      }, GUEST);
      await page.waitForTimeout(2200);
      const guestId = (await get(`/seasons/${season.id}`)).games[0].confirmed_drop_ins.find(
        (d) => d.player_name === GUEST
      ).player_id;
      const ledger = await get(`/clubs/${club.id}/players/${guestId}/ledger`);
      const payments = ledger.entries.filter((e) => e.entry_type === "payment");
      report("按下已收之後，帳上真的記了一筆", [
        ...(payments.length === 1 ? [] : [`收款紀錄有 ${payments.length} 筆`]),
        ...(Number(ledger.balance) === 0 ? [] : [`收完之後餘額是 ${ledger.balance}，應該是 0`]),
        ...errors,
      ]);
    }
  } finally {
    await browser.close();
    await fetch(`${API}/clubs/${club.id}`, { method: "DELETE", headers: asOrganizer });
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n候補遞補到收款都正常。");
  process.exit(failed ? 1 : 0);
})();
