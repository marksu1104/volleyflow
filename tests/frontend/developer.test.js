// 後台總覽 (developer.html), rebuilt 2026-10-22: the audit trail read as
// sentences, the server's errors, and a club list that folds away clubs
// with nothing on their books.
//
// The sentences are where a wrong word would hide. Each action key is
// written by the server (routes/_audit.py) and has to read with the same
// words as the button that made it — 收款、復原、場地費照算 — so a key
// added there without a sentence here falls through to 「執行 xxx」,
// which the first test refuses.

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { load } = require("./harness.js");

const page = load("developer.html");
const {
  activityKind,
  activityRowHtml,
  activitySentence,
  isEmptyClub,
  locationLabel,
  pageName,
  visibleClubs,
} = page;

const event = (over) => ({
  actor_name: "管理員甲",
  subject_name: "成員乙",
  club_name: "週二排球",
  amount: null,
  detail: null,
  occurred_at: "2026-10-22T12:00:00",
  ...over,
});

test("every action the server records has a sentence of its own", () => {
  const source = fs.readFileSync(
    path.join(__dirname, "..", "..", "src", "volleyflow", "api", "routes", "_audit.py"),
    "utf8"
  );
  const block = /ACTIONS = frozenset\(\s*\{([\s\S]*?)\}\s*\)/.exec(source);
  assert.ok(block, "ACTIONS not found in _audit.py");
  const actions = [...block[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  assert.ok(actions.length >= 20, `only ${actions.length} actions read`);

  for (const action of actions) {
    const sentence = activitySentence(
      event({ action, amount: "100", detail: "2026-10-14" })
    );
    assert.doesNotMatch(sentence, /執行/, `${action} has no sentence`);
  }
});

test("a payment names who paid, how much, and for which night", () => {
  const sentence = activitySentence(
    event({ action: "payment", amount: "285", detail: "2026-10-14" })
  );

  assert.equal(sentence, "管理員甲 記錄 成員乙 收款 $285（10/14 場次）");
});

test("a negative payment reads as a refund, without a minus sign", () => {
  const sentence = activitySentence(event({ action: "payment", amount: "-120" }));

  assert.equal(sentence, "管理員甲 記錄 成員乙 退款 $120");
});

test("a new venue cost says what it was before", () => {
  const sentence = activitySentence(
    event({ action: "venue_cost_changed", amount: "12000", detail: "10000" })
  );

  assert.equal(sentence, "管理員甲 將場地費由 $10000 改為 $12000");
});

test("a new capacity is counted in people, not dollars", () => {
  const sentence = activitySentence(
    event({ action: "capacity_changed", amount: "20", detail: "18" })
  );

  assert.equal(sentence, "管理員甲 將人數上限由 18 人改為 20 人");
});

test("air conditioning that did not run takes the surcharge off", () => {
  const sentence = activitySentence(
    event({ action: "ac_off", amount: "-800", detail: "2026-10-14" })
  );

  assert.equal(sentence, "管理員甲 記錄 10/14 實際未開冷氣，場地費 −$800");
});

test("a season is named by its first and last game", () => {
  const sentence = activitySentence(
    event({ action: "season_settled", detail: "2026-09-15~2026-12-08" })
  );

  assert.equal(sentence, "管理員甲 完成季末結算（9/15–12/8）");
});

test("a cancelled game says which way the venue cost went", () => {
  const free = activitySentence(event({ action: "game_cancelled_refunded", detail: "2026-10-14" }));
  const paid = activitySentence(event({ action: "game_cancelled", detail: "2026-10-14" }));

  assert.equal(free, "管理員甲 取消 10/14 場次，場地費不算這場");
  assert.equal(paid, "管理員甲 取消 10/14 場次，場地費照算");
});

test("money, departures and the rest are told apart", () => {
  assert.equal(activityKind("payment"), "money");
  assert.equal(activityKind("ac_on"), "money");
  assert.equal(activityKind("member_removed"), "remove");
  assert.equal(activityKind("game_cancelled_refunded"), "remove");
  assert.equal(activityKind("roster_added"), "");
});

test("names in the trail are escaped", () => {
  const html = activityRowHtml(
    event({ action: "member_removed", subject_name: "<img src=x onerror=alert(1)>" })
  );

  assert.doesNotMatch(html, /<img/);
  assert.match(html, /&lt;img/);
});

const club = (over) => ({
  id: 1,
  name: "週二排球",
  organizers: ["管理員甲"],
  members: 12,
  seasons: 1,
  last_activity: "2026-10-20T12:00:00",
  ...over,
});

test("a club with nothing on its books is folded away by default", () => {
  const used = club({ id: 1 });
  const unused = club({ id: 2, name: "測試", seasons: 0, last_activity: null });

  assert.ok(isEmptyClub(unused));
  assert.ok(!isEmptyClub(used));
  assert.deepEqual(visibleClubs([used, unused], "", false).map((c) => c.id), [1]);
  assert.deepEqual(visibleClubs([used, unused], "", true).map((c) => c.id), [1, 2]);
});

test("a season with nobody on it does not make a club in use", () => {
  // What a test run leaves behind: a club, a season, no members, and so
  // no fee ever charged.
  const leftover = club({ id: 2, members: 0, seasons: 1, last_activity: null });

  assert.ok(isEmptyClub(leftover));
});

test("a search finds a folded-away club too", () => {
  const unused = club({ id: 2, name: "測試球隊", seasons: 0, last_activity: null });

  assert.deepEqual(visibleClubs([club(), unused], "測試", false).map((c) => c.id), [2]);
});

test("a search matches an organizer's name", () => {
  const other = club({ id: 3, name: "週四排球", organizers: ["管理員丙"] });

  assert.deepEqual(visibleClubs([club(), other], "丙", false).map((c) => c.id), [3]);
});

test("clubs used most recently come first", () => {
  const older = club({ id: 1, last_activity: "2026-09-01T12:00:00" });
  const newer = club({ id: 2, last_activity: "2026-10-21T12:00:00" });

  assert.deepEqual(visibleClubs([older, newer], "", false).map((c) => c.id), [2, 1]);
});

test("an error's location reads as file, line and function", () => {
  assert.equal(
    locationLabel("seasons.py:2173 in add_member"),
    "seasons.py 第 2173 行・add_member"
  );
  assert.equal(locationLabel("(unknown location)"), "(unknown location)");
});

test("an error's page is named the way people know it", () => {
  assert.equal(pageName("member.html"), "會員頁");
  assert.equal(pageName("organizer-ledger.html"), "管理・帳務");
  assert.equal(pageName(null), "頁面不明");
  assert.equal(pageName("new-page.html"), "new-page.html");
});
