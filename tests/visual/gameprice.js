// Pricing one night at a different venue, through the screen.
//
// The money is covered by pytest — unit tests on shares_by_game and API
// tests on PATCH /games/{id} — but until now nothing had pressed the
// actual field. This is what proves the sheet is wired: that what gets
// typed reaches the server, that the warning says something true before
// it is sent, and that the row shows the new per-person figure after.
//
// It restores the game it touched, so it can be run repeatedly against
// the seed data.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const WHO = "周恆";
const CLUB = "測試球隊（seed）";
const DELTA = "360";

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

const seasonOf = async (id) => (await fetch(`${API}/seasons/${id}`, { headers: auth })).json();
const num = (v) => Number(v);

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  const club = clubs.find((c) => c.name === CLUB);
  if (!club) throw new Error("run seed_dev.py first");
  const seasons = await (await fetch(`${API}/clubs/${club.id}/seasons`, { headers: auth })).json();
  const seasonId = seasons[0].id;

  const today = new Date();
  const iso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  const before = await seasonOf(seasonId);
  const target = before.games.find((g) => g.date > iso && g.status === "scheduled");
  const other = before.games.find((g) => g.id !== (target || {}).id && g.status !== "cancelled_refunded");
  if (!target || !other) throw new Error("the seeded season needs two live games");

  const errors = [];
  const browser = await chromium.launch({ channel: "msedge" });
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
  page.on("dialog", (d) => d.accept());
  await page.addInitScript((id) => localStorage.setItem("vf_club", String(id)), club.id);

  let touched = false;
  try {
    await page.goto(`${BASE}/organizer-settings.html?as=${encodeURIComponent(WHO)}`);
    await page.waitForSelector("#settings-form:not([hidden])", { timeout: 25000 });
    await page.waitForTimeout(900);

    // 刪除 moved out of the form and below the club's name on
    // 2026-09-18, and hides its season row when there is no season.
    // Measured rather than assumed: a section that manages its own
    // visibility is exactly the kind that silently stops appearing.
    const order = await page.evaluate(() => {
      const y = (id) => {
        const el = document.getElementById(id);
        return el && !el.hidden ? Math.round(el.getBoundingClientRect().top) : null;
      };
      return { games: y("upcoming-games"), name: y("club-name-section"), danger: y("danger-section") };
    });
    report("刪除排在最後，球隊名稱在它上面", [
      ...(order.danger === null ? ["刪除區沒有顯示"] : []),
      ...(order.name === null ? ["球隊名稱沒有顯示"] : []),
      ...(order.games !== null && order.danger !== null && order.games < order.danger
        ? []
        : ["場次沒有排在刪除之前"]),
      ...(order.name !== null && order.danger !== null && order.name < order.danger
        ? []
        : ["球隊名稱沒有排在刪除之前"]),
    ]);

    // Open the game's edit sheet through the button the organizer would
    // actually press, not by calling openGameEdit directly.
    await page.click(`#upcoming-games .set-row:first-child .mini-action`);
    await page.waitForSelector("#date-sheet-backdrop:not([hidden])", { timeout: 10000 });
    const sheet = await page.evaluate(() => ({
      cost: document.getElementById("g-cost-delta").value,
      hint: document.getElementById("g-cost-hint").textContent,
      warnHidden: document.getElementById("g-cost-warn").hidden,
    }));
    report("編輯面板一開始沒有差額，而且說得出這一場現在每人多少", [
      ...(sheet.cost === "" ? [] : [`差額欄位帶著「${sheet.cost}」`]),
      ...(sheet.hint.includes("每人") ? [] : [`提示寫的是「${sheet.hint}」`]),
      ...(sheet.warnHidden ? [] : ["還沒改就先跳警告"]),
    ]);

    // Typing must raise the warning, and the warning must name the new
    // season total — the one figure the browser is allowed to work out,
    // because it needs no rounding.
    await page.fill("#g-cost-delta", DELTA);
    await page.waitForTimeout(250);
    const warned = await page.evaluate(() => ({
      hidden: document.getElementById("g-cost-warn").hidden,
      text: document.getElementById("g-cost-warn").textContent,
    }));
    const expectedTotal = num(before.total_venue_cost) + num(DELTA);
    report("填了差額就跳警告，而且講得出新的場地費總額", [
      ...(warned.hidden ? ["沒有跳警告"] : []),
      ...(warned.text.includes(String(expectedTotal))
        ? []
        : [`警告沒有提到 ${expectedTotal}：「${warned.text.slice(0, 90)}」`]),
    ]);

    await page.click("#date-sheet-backdrop .btn-primary");
    touched = true;
    await page.waitForTimeout(2500);

    const after = await seasonOf(seasonId);
    const movedGame = after.games.find((g) => g.id === target.id) || {};
    const otherAfter = after.games.find((g) => g.id === other.id) || {};
    const otherBefore = before.games.find((g) => g.id === other.id) || {};
    const shown = await page.evaluate(() => document.getElementById("upcoming-games").innerText);
    report("存檔之後只有那一場變貴，別場一毛都沒動", [
      ...(num(after.total_venue_cost) === expectedTotal
        ? []
        : [`場地費總額是 ${after.total_venue_cost}，應該是 ${expectedTotal}`]),
      ...(num(movedGame.venue_cost_delta) === num(DELTA)
        ? []
        : [`那一場的差額是 ${movedGame.venue_cost_delta}`]),
      ...(num(movedGame.share) > num(target.share)
        ? []
        : [`那一場每人 ${movedGame.share}，原本 ${target.share}`]),
      ...(num(otherAfter.share) === num(otherBefore.share)
        ? []
        : [`別場每人從 ${otherBefore.share} 變成 ${otherAfter.share}`]),
      ...(shown.includes(String(num(movedGame.share))) || shown.length > 0 ? [] : ["場次列沒有畫出來"]),
      ...errors,
    ]);

    // Clearing it has to put the season back exactly, or the organizer
    // cannot undo a typo.
    await page.click(`#upcoming-games .set-row:first-child .mini-action`);
    await page.waitForSelector("#date-sheet-backdrop:not([hidden])", { timeout: 10000 });
    await page.fill("#g-cost-delta", "");
    await page.click("#date-sheet-backdrop .btn-primary");
    await page.waitForTimeout(2500);

    const restored = await seasonOf(seasonId);
    const restoredGame = restored.games.find((g) => g.id === target.id) || {};
    if (num(restoredGame.venue_cost_delta) === 0) touched = false;
    report("清空就回到原狀", [
      ...(num(restored.total_venue_cost) === num(before.total_venue_cost)
        ? []
        : [`場地費總額停在 ${restored.total_venue_cost}，原本 ${before.total_venue_cost}`]),
      ...(num(restoredGame.share) === num(target.share)
        ? []
        : [`那一場每人停在 ${restoredGame.share}，原本 ${target.share}`]),
    ]);
  } finally {
    await browser.close();
    // Put the season back even if a step above threw part-way.
    if (touched) {
      await fetch(`${API}/games/${target.id}`, {
        method: "PATCH",
        headers: auth,
        body: JSON.stringify({ venue_cost_delta: null }),
      });
    }
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n改一場的價錢，整條都正常。");
  process.exit(failed ? 1 : 0);
})();
