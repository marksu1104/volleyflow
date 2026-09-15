// Opening an invite link and joining, the way a stranger actually does.
//
// This is the first thing every new user of the app does, and on
// 2026-09-15 it changed underneath them: joining started requiring the
// link's token (an id alone had let anybody join every club in turn — see
// tests/api/test_tenant_isolation.py). The API side is covered there.
// What only a browser can show is whether the page still gets somebody
// from a shared link into the club — whether it keeps the token from the
// URL all the way to the join button, and whether leaving the invite
// screen and coming back still has it.
//
//     .\scripts\dev-api.ps1      (in one window)
//     .\scripts\dev-web.ps1      (in another)
//     uv run python scripts/seed_dev.py
//     node tests/visual/join.js
//
// Adds a member to the seed club — re-run seed_dev.py afterwards if that
// matters to whatever runs next.

const { chromium } = require("playwright");

const BASE = "http://localhost:5500";
const API = "http://localhost:8000";
const ORGANIZER = "周恆";
const CLUB = "測試球隊（seed）";

const bearer = (name) => ({ Authorization: `Bearer dev:${encodeURIComponent(name)}` });

let failed = 0;
function report(name, problems) {
  console.log(`${problems.length ? "FAIL" : "ok  "} ${name}`);
  for (const p of problems) console.log(`       ${p}`);
  if (problems.length) failed += 1;
}

async function inviteToken() {
  const clubs = await (await fetch(`${API}/clubs`, { headers: bearer(ORGANIZER) })).json();
  const club = clubs.find((c) => c.name === CLUB);
  if (!club) throw new Error(`the seed club isn't there — run seed_dev.py first`);
  const invite = await (
    await fetch(`${API}/clubs/${club.id}/invite`, { headers: bearer(ORGANIZER) })
  ).json();
  return { clubId: club.id, token: invite.token };
}

async function open(browser, url) {
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 120)}`));
  page.on("response", (r) => {
    if (r.url().startsWith(API) && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`);
  });
  await page.goto(url, { waitUntil: "domcontentloaded" });
  return { page, errors };
}

const heroSays = (page) =>
  page.evaluate(() => document.getElementById("hero-wrap").innerText.replace(/\s+/g, " ").trim());

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  const { clubId, token } = await inviteToken();
  const stranger = "新朋友" + (Date.now() % 100000);

  // 1. The link names the club, and offers to join.
  const { page, errors } = await open(
    browser,
    `${BASE}/member.html?invite=${encodeURIComponent(token)}&as=${encodeURIComponent(stranger)}`
  );
  await page.waitForFunction(() => /加入「/.test(document.body.innerText), null, { timeout: 20000 }).catch(() => {});
  const first = await heroSays(page);
  report("an invite link names the club and offers to join", [
    ...(first.includes(`加入「${CLUB}」`) ? [] : [`the screen says: ${first.slice(0, 80)}`]),
  ]);

  // 2. Leaving the invite screen through the club list and coming back
  // still lands on the same invite. This used to navigate to ?club=<id>,
  // which nothing read — harmless while joining needed no token, fatal
  // after. It now goes to ?invite=<token>, and the page's own boot logic
  // strips that from the address bar on arrival (see the comment on
  // `inviteResolved`) the same way it does on the very first load — so
  // the address bar being clean here is correct, not a regression, and
  // the thing actually worth checking is that the invite screen is what
  // greets the reload.
  await page.click("#club-chip").catch(() => {});
  await page.waitForTimeout(400);
  const tapped = await page.evaluate(() => {
    const row = [...document.querySelectorAll("[data-pick-club]")].find((r) =>
      r.textContent.includes("尚未加入")
    );
    if (!row) return false;
    row.click();
    return true;
  });
  await page.waitForTimeout(3500);
  const back = await heroSays(page);
  report("coming back to the invite from the club list lands on it again", [
    ...(tapped ? [] : ["no not-yet-joined club in the club list to tap"]),
    ...(back.includes(`加入「${CLUB}」`) ? [] : [`the screen says: ${back.slice(0, 80)}`]),
  ]);

  // 3. Joining works from the page, and then asks which kind of member.
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("#hero-wrap button, #hero-wrap a")].find((x) =>
      x.textContent.includes("加入球隊")
    );
    if (b) b.click();
  });
  await page.waitForFunction(() => /已加入「/.test(document.body.innerText), null, { timeout: 15000 }).catch(() => {});
  const joined = await heroSays(page);
  report("pressing 加入球隊 joins, and asks which kind of member", [
    ...(joined.includes(`已加入「${CLUB}」`) ? [] : [`the screen says: ${joined.slice(0, 80)}`]),
  ]);

  // 4. Answering lets them in, and the server agrees they are a member.
  await page.evaluate(() => {
    const b = [...document.querySelectorAll("#hero-wrap button")].find((x) =>
      x.textContent.includes("我只是臨打")
    );
    if (b) b.click();
  });
  await page.waitForTimeout(4000);
  const me = await (
    await fetch(`${API}/players/identify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: `dev:${encodeURIComponent(stranger)}`, display_name: stranger }),
    })
  ).json();
  const theirClubs = await (
    await fetch(`${API}/players/${me.id}/clubs`, { headers: bearer(stranger) })
  ).json();
  const member = Array.isArray(theirClubs) && theirClubs.some((c) => c.id === clubId);
  report("afterwards the server has them as a member of that club", [
    ...(member ? [] : [`their clubs: ${JSON.stringify(theirClubs).slice(0, 100)}`]),
    ...errors,
  ]);
  await page.close();

  // 4b. The same link again, now as a member, opens the club. It used to
  // say 找不到這個球隊: the page dropped the club but kept the token.
  const again = await open(
    browser,
    `${BASE}/member.html?invite=${encodeURIComponent(token)}&as=${encodeURIComponent(stranger)}`
  );
  await again.page
    .waitForFunction(() => document.getElementById("club-chip-name").textContent !== "選擇球隊", null, {
      timeout: 20000,
    })
    .catch(() => {});
  await again.page.waitForTimeout(1500);
  const reopened = await heroSays(again.page);
  const chipName = await again.page.textContent("#club-chip-name");
  report("opening the link again as a member opens the club", [
    ...(reopened.includes("找不到這個球隊") ? [`the screen says: ${reopened.slice(0, 80)}`] : []),
    ...(chipName === CLUB ? [] : [`the club chip says "${chipName}"`]),
    ...again.errors,
  ]);
  await again.page.close();

  // 5. A forged link goes nowhere, and says so.
  const forged = await open(
    browser,
    `${BASE}/member.html?invite=${clubId}.00000000000000000000&as=${encodeURIComponent(stranger + "乙")}`
  );
  await forged.page.waitForFunction(() => /找不到這個球隊|加入「/.test(document.body.innerText), null, { timeout: 20000 }).catch(() => {});
  const says = await heroSays(forged.page);
  report("a forged link is refused, with a way back", [
    ...(says.includes("找不到這個球隊") ? [] : [`the screen says: ${says.slice(0, 80)}`]),
    ...forged.errors,
  ]);

  await browser.close();
  console.log(failed ? `\n${failed} 項有問題。` : "\n從邀請連結加入球隊的每一步都正常。");
  process.exit(failed ? 1 : 0);
})();
