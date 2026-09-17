// Moving a game to another date, from the settings page.
//
// The venue shifts a booking and the organizer has to say so without
// calling the night off. Everything already recorded against that game
// has to travel with it, and two games may not share an evening.

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

/** A date as the app writes it. Not toISOString(): the value is parsed
 * as midnight in Taiwan (UTC+8), so going back through UTC takes the
 * added day off again — which had this check asking the server to move
 * a game to the date it was already on, and reading the refusal to do
 * anything as a bug. */
const iso = (d) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

const seasonOf = async (id) => (await fetch(`${API}/seasons/${id}`, { headers: auth })).json();

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  const club = clubs.find((c) => c.name === CLUB);
  if (!club) throw new Error("run seed_dev.py first");
  const seasons = await (await fetch(`${API}/clubs/${club.id}/seasons`, { headers: auth })).json();
  const before = await seasonOf(seasons[0].id);

  const today = iso(new Date());
  const target = before.games.find((g) => g.date > today && g.status === "scheduled");
  if (!target) throw new Error("the seeded season has no game still to come");
  const busyBefore = {
    away: target.absences.length,
    playing: target.confirmed_drop_ins.length,
  };

  const errors = [];
  const browser = await chromium.launch({ channel: "msedge" });
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
  page.on("dialog", (d) => d.accept());
  await page.addInitScript((id) => localStorage.setItem("vf_club", String(id)), club.id);

  let movedTo = null;
  try {
    await page.goto(`${BASE}/organizer-settings.html?as=${encodeURIComponent(WHO)}`);
    await page.waitForSelector("#settings-form:not([hidden])", { timeout: 20000 });
    await page.waitForTimeout(900);

    // The club-name field, checked here because this is the only
    // permanent check that opens the settings page. It was once a row
    // that collapsed into itself — `.btn { width: 100% }` — so its shape
    // is worth measuring, not just its presence.
    const nameField = await page.evaluate(() => {
      const input = document.getElementById("club-name");
      const save = document.getElementById("club-name-save");
      return {
        value: input.value,
        inputWidth: Math.round(input.getBoundingClientRect().width),
        saveWidth: Math.round(save.getBoundingClientRect().width),
        saveDisabled: save.disabled,
      };
    });
    report("球隊名稱是一個完整寬度的欄位，旁邊一顆小的儲存", [
      ...(nameField.value === CLUB ? [] : [`欄位裡是「${nameField.value}」`]),
      ...(nameField.inputWidth >= 240 ? [] : [`輸入框只有 ${nameField.inputWidth}px 寬`]),
      ...(nameField.saveWidth <= 110 ? [] : [`儲存鍵有 ${nameField.saveWidth}px 寬`]),
      ...(nameField.saveDisabled ? [] : ["還沒改就可以按儲存"]),
    ]);

    const offered = await page.evaluate(() => {
      const rows = [...document.querySelectorAll("#upcoming-games .set-row")];
      return {
        rows: rows.length,
        withMove: rows.filter((r) => r.textContent.includes("編輯")).length,
        withCancel: rows.filter((r) => r.textContent.includes("取消")).length,
      };
    });
    report("還沒打的場次都可以編輯", [
      ...(offered.rows > 0 ? [] : ["設定頁沒有列出任何場次"]),
      ...(offered.withMove === offered.rows ? [] : [`${offered.rows} 列裡只有 ${offered.withMove} 列能改`]),
      ...(offered.withCancel === offered.rows ? [] : ["取消不見了"]),
    ]);

    // A day later — the seeded games are a week apart, so it is free.
    await page.click("#upcoming-games .set-row:first-child .mini-action");
    await page.waitForSelector("#date-sheet-backdrop:not([hidden])", { timeout: 10000 });
    const sheet = await page.evaluate(() => ({
      title: document.getElementById("date-sheet-title").textContent,
      value: document.getElementById("date-sheet-input").value,
    }));
    const next = new Date(sheet.value + "T00:00:00");
    next.setDate(next.getDate() + 1);
    movedTo = iso(next);

    // Through the DOM rather than by typing: a date input is three
    // little fields, and a half-typed one submits what it opened with.
    await page.evaluate((value) => {
      const el = document.getElementById("date-sheet-input");
      el.value = value;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
    }, movedTo);
    await page.click("#date-sheet-backdrop .btn-primary");
    await page.waitForTimeout(2500);

    const after = await seasonOf(seasons[0].id);
    const moved = after.games.find((g) => g.id === target.id) || {};
    const shown = await page.evaluate(() => document.getElementById("upcoming-games").innerText);
    report("改完之後日期真的變了，記錄也跟著走", [
      ...(sheet.title.includes("改到別天") ? [] : [`面板標題是 ${sheet.title}`]),
      ...(sheet.value === target.date ? [] : [`面板帶的是 ${sheet.value}，該場是 ${target.date}`]),
      ...(moved.date === movedTo ? [] : [`伺服器上是 ${moved.date}，應該是 ${movedTo}`]),
      ...(shown.includes(`${next.getMonth() + 1}/${next.getDate()}`) ? [] : ["畫面上沒有看到新的日期"]),
      ...((moved.absences || []).length === busyBefore.away ? [] : ["請假紀錄沒跟著搬"]),
      ...((moved.confirmed_drop_ins || []).length === busyBefore.playing ? [] : ["報名紀錄沒跟著搬"]),
      ...errors,
    ]);

    const other = after.games.find((g) => g.id !== target.id && g.date > today);
    if (other) {
      await page.click("#upcoming-games .set-row:first-child .mini-action");
      await page.waitForSelector("#date-sheet-backdrop:not([hidden])", { timeout: 10000 });
      await page.evaluate((value) => {
        const el = document.getElementById("date-sheet-input");
        el.value = value;
        el.dispatchEvent(new Event("change", { bubbles: true }));
      }, other.date);
      await page.click("#date-sheet-backdrop .btn-primary");
      await page.waitForTimeout(1500);
      const said = await page.evaluate(() => {
        const el = document.querySelector(".toast");
        return el ? el.textContent : "";
      });
      const still = await seasonOf(seasons[0].id);
      report("兩場不能排在同一天", [
        ...(said.includes("已經有一場") ? [] : [`畫面說的是「${said}」`]),
        ...(still.games.find((g) => g.id === target.id).date === movedTo ? [] : ["日期還是被改掉了"]),
      ]);
      await page.evaluate(() => closeDateSheet());
    }
  } finally {
    await browser.close();
    if (movedTo) {
      await fetch(`${API}/games/${target.id}`, {
        method: "PATCH",
        headers: auth,
        body: JSON.stringify({ date: target.date }),
      });
    }
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n編輯整條都正常。");
  process.exit(failed ? 1 : 0);
})();
