// Two tabs, two clubs. Opening a second club in a second tab must not
// move the first tab underneath itself.
//
// Reported on 2026-09-17: a club created minutes earlier showed 21
// members and told its own organizer they were not the organizer. It was
// not the database — the club id lived in localStorage, which every tab
// of a browser shares, so the second tab rewrote it and the first tab
// went on fetching the *other* club's roster under this club's season.
// See rememberedId in shared.js.

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

(async () => {
  const clubs = await (await fetch(`${API}/clubs`, { headers: auth })).json();
  const seed = clubs.find((c) => c.name === CLUB);
  if (!seed) throw new Error("run seed_dev.py first");
  const seasons = await (await fetch(`${API}/clubs/${seed.id}/seasons`, { headers: auth })).json();
  const season = await (await fetch(`${API}/seasons/${seasons[0].id}`, { headers: auth })).json();

  const second = await (
    await fetch(`${API}/clubs`, {
      method: "POST",
      headers: auth,
      body: JSON.stringify({ name: "另一隊" }),
    })
  ).json();

  const browser = await chromium.launch({ channel: "msedge" });
  const context = await browser.newContext({ viewport: { width: 390, height: 900 } });
  const errors = [];

  try {
    // Tab one, on the seeded club.
    const first = await context.newPage();
    first.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
    await first.addInitScript((id) => localStorage.setItem("vf_club", String(id)), seed.id);
    await first.goto(`${BASE}/organizer-settings.html?as=${encodeURIComponent(WHO)}`);
    await first.waitForSelector("#settings-form:not([hidden])", { timeout: 20000 });
    const before = await first.evaluate(() => currentClubId());

    // Tab two, switched to the brand new club.
    const other = await context.newPage();
    other.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 140)}`));
    await other.goto(`${BASE}/organizer-settings.html?as=${encodeURIComponent(WHO)}`);
    await other.waitForTimeout(1500);
    await other.selectOption("#club-picker", String(second.id));
    await other.waitForTimeout(1500);
    const otherClub = await other.evaluate(() => currentClubId());

    // Tab one must still be where it was, and must still fetch its own.
    await first.bringToFront();
    const after = await first.evaluate(() => currentClubId());
    await first.click("text=開新一季");
    await first.waitForSelector("#create-season-form:not([hidden])", { timeout: 10000 });
    await first.click('[data-go="2"]');
    await first.waitForTimeout(200);
    await first.click('[data-go="3"]');
    await first.waitForTimeout(1500);
    const roster = await first.evaluate(() => ({
      rows: document.querySelectorAll("#roster-list .pick-row").length,
      organizerTags: document.querySelectorAll("#roster-list .role-tag").length,
    }));

    report("第二個分頁切換球隊，不會動到第一個分頁", [
      ...(otherClub === String(second.id) ? [] : [`第二個分頁停在 ${otherClub}`]),
      ...(after === before ? [] : [`第一個分頁從 ${before} 被改成 ${after}`]),
    ]);
    report("第一個分頁抓到的仍是自己那一隊的名單", [
      ...(roster.rows >= season.members.length
        ? []
        : [`名單只有 ${roster.rows} 人，這一季有 ${season.members.length} 人`]),
      ...(roster.organizerTags >= 1 ? [] : ["名單裡沒有管理員，八成抓到別隊的人"]),
      ...errors,
    ]);
  } finally {
    await browser.close();
    await fetch(`${API}/clubs/${second.id}`, { method: "DELETE", headers: auth });
  }

  console.log(failed ? `\n${failed} 項有問題。` : "\n兩個分頁各看各的球隊。");
  process.exit(failed ? 1 : 0);
})();
