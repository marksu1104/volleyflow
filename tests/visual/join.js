// Opening an invite link, asking to join, and being let in by the
// organizer — the way a stranger actually gets into a club.
//
// Joining changed twice: on 2026-09-15 it started requiring the link's
// token (an id alone let anybody join every club), and on 2026-09-16 it
// stopped letting people straight in: asking puts them in a queue and the
// organizer approves. The API side is in tests/api/test_join_approval.py
// and test_tenant_isolation.py; what only a browser can show is whether a
// real person can get from a shared link to the club without getting lost.
//
//     .\scripts\dev-api.ps1      (in one window)
//     .\scripts\dev-web.ps1      (in another)
//     uv run python scripts/seed_dev.py
//     node tests/visual/join.js
//
// Adds a member to the seed club — re-run seed_dev.py afterwards.

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
  if (!club) throw new Error("the seed club isn't there — run seed_dev.py first");
  const invite = await (await fetch(`${API}/clubs/${club.id}/invite`, { headers: bearer(ORGANIZER) })).json();
  return { clubId: club.id, token: invite.token };
}

async function open(browser, url, clubId) {
  const page = await browser.newPage({ viewport: { width: 390, height: 900 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(`JS: ${String(e).slice(0, 120)}`));
  page.on("response", (r) => {
    if (r.url().startsWith(API) && r.status() >= 500) errors.push(`${r.status()} ${r.url()}`);
  });
  page.on("dialog", (d) => d.accept());
  if (clubId) await page.addInitScript((id) => localStorage.setItem("vf_club", String(id)), clubId);
  await page.goto(url, { waitUntil: "domcontentloaded" });
  return { page, errors };
}

const heroSays = (page) =>
  page.evaluate(() => document.getElementById("hero-wrap").innerText.replace(/\s+/g, " ").trim());
const waitForText = (page, pattern) =>
  page
    .waitForFunction((src) => new RegExp(src).test(document.body.innerText), pattern.source, { timeout: 20000 })
    .catch(() => {});

async function statusIn(name, clubId) {
  const me = await (
    await fetch(`${API}/players/identify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: `dev:${encodeURIComponent(name)}`, display_name: name }),
    })
  ).json();
  const clubs = await (await fetch(`${API}/players/${me.id}/clubs`, { headers: bearer(name) })).json();
  const mine = Array.isArray(clubs) ? clubs.find((c) => c.id === clubId) : null;
  return mine ? mine.status : "none";
}

(async () => {
  const browser = await chromium.launch({ channel: "msedge" });
  const { clubId, token } = await inviteToken();
  const stranger = "新朋友" + (Date.now() % 100000);
  const link = `${BASE}/member.html?invite=${encodeURIComponent(token)}&as=${encodeURIComponent(stranger)}`;

  // 1. The link names the club and offers the two ways to ask.
  const { page, errors } = await open(browser, link);
  await waitForText(page, /加入「/);
  const first = await heroSays(page);
  report("an invite link names the club and offers 固定成員 / 臨打成員", [
    ...(first.includes(`加入「${CLUB}」`) ? [] : [`the screen says: ${first.slice(0, 80)}`]),
    ...(first.includes("臨打成員") && first.includes("固定成員") ? [] : ["the two buttons aren't both there"]),
  ]);

  // 2. Asking says it's been sent, and the server has them waiting.
  // text= is a substring match, so this must stay distinct from
  // 固定成員 beside it — renaming either button means editing here too,
  // or this line stops finding anything and the check dies on a timeout
  // instead of reporting what changed.
  await page.click("text=臨打成員");
  await waitForText(page, /已送出申請/);
  const asked = await heroSays(page);
  const waiting = await statusIn(stranger, clubId);
  report("臨打成員 says the request is in, and the server has them waiting", [
    ...(asked.includes("已送出申請") ? [] : [`the screen says: ${asked.slice(0, 80)}`]),
    ...(waiting === "pending" ? [] : [`their membership is ${waiting}`]),
    ...errors,
  ]);
  await page.close();

  // 3. Opening the link again while waiting says so — not 找不到, not an app of refusals.
  const again = await open(browser, link);
  await waitForText(again.page, /已送出申請|找不到|加入「/);
  const reopened = await heroSays(again.page);
  report("opening the link again while waiting shows the request, not an error", [
    ...(reopened.includes("已送出申請") ? [] : [`the screen says: ${reopened.slice(0, 80)}`]),
    ...again.errors,
  ]);
  await again.page.close();

  // 4. The organizer sees a count on 管理, and approves from 管理成員.
  const home = await open(browser, `${BASE}/member.html?as=${encodeURIComponent(ORGANIZER)}`, clubId);
  await home.page.waitForSelector("#manage-badge:not([hidden])", { timeout: 20000 }).catch(() => {});
  const badge = await home.page.evaluate(() => {
    const el = document.getElementById("manage-badge");
    return { hidden: el.hidden, text: el.textContent, href: document.getElementById("manage-link").getAttribute("href") };
  });
  report("the organizer's 管理 carries a count and leads to 管理成員", [
    ...(!badge.hidden && Number(badge.text) >= 1 ? [] : [`badge: ${JSON.stringify(badge)}`]),
    ...(badge.href === "organizer-members.html" ? [] : [`管理 goes to ${badge.href}`]),
    ...home.errors,
  ]);
  await home.page.close();

  const members = await open(browser, `${BASE}/organizer-members.html?as=${encodeURIComponent(ORGANIZER)}`, clubId);
  await members.page.waitForSelector("#requests-section:not([hidden])", { timeout: 20000 }).catch(() => {});
  const row = members.page.locator("#requests-list .pool-row", { hasText: stranger });
  await row
    .locator("button", { hasText: "臨打", exact: true })
    .click()
    .catch((e) => members.errors.push(`couldn't press 臨打 on ${stranger}: ${String(e).slice(0, 100)}`));
  await members.page.waitForTimeout(2500);
  const approved = await statusIn(stranger, clubId);
  report("pressing 臨打 in 待核准 lets them in", [
    ...(approved === "active" ? [] : [`their membership is ${approved}`]),
    ...members.errors,
  ]);
  await members.page.close();

  // 5. Now the same person opening the app is in the club.
  const inside = await open(browser, `${BASE}/member.html?as=${encodeURIComponent(stranger)}`, clubId);
  await inside.page
    .waitForFunction((n) => document.getElementById("club-chip-name").textContent === n, CLUB, { timeout: 20000 })
    .catch(() => {});
  await inside.page.waitForTimeout(1500);
  const now = await heroSays(inside.page);
  report("once approved, opening the app lands in the club", [
    ...(/已送出申請|找不到/.test(now) ? [`the screen says: ${now.slice(0, 80)}`] : []),
    ...((await inside.page.textContent("#club-chip-name")) === CLUB ? [] : ["the club chip doesn't name the club"]),
    ...inside.errors,
  ]);
  await inside.page.close();

  // 6. A forged link goes nowhere, and says so.
  const forged = await open(
    browser,
    `${BASE}/member.html?invite=${clubId}.00000000000000000000&as=${encodeURIComponent(stranger + "乙")}`
  );
  await waitForText(forged.page, /找不到這個球隊|加入「/);
  const says = await heroSays(forged.page);
  report("a forged link is refused, with a way back", [
    ...(says.includes("找不到這個球隊") ? [] : [`the screen says: ${says.slice(0, 80)}`]),
    ...forged.errors,
  ]);

  await browser.close();
  console.log(failed ? `\n${failed} 項有問題。` : "\n從邀請連結申請、被核准、進到球隊，每一步都正常。");
  process.exit(failed ? 1 : 0);
})();
