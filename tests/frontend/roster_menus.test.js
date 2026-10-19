// Tapping a row opens it in place onto at most two buttons (2026-10-20,
// replacing a menu from the bottom of the screen). 銷假 is 取消請假, and
// 指定代打 on somebody still playing takes leave in the same step.

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, makeElement } = require("./harness.js");

const { applyAbsenceWithSubstituteLocally, renderGameDetail } = load("member.html");

function sheet(gameOverrides = {}) {
  const game = {
    id: 9,
    date: "2031-08-19",
    status: "scheduled",
    locked: false,
    past_deadline: false,
    share: "235",
    absences: [],
    confirmed_drop_ins: [],
    waitlist_entries: [],
    ...gameOverrides,
  };
  const season = {
    id: 1,
    capacity: 3,
    settled_at: null,
    members: [
      { id: 1, name: "Alice", gender: "female" },
      { id: 2, name: "Bob", gender: "male" },
    ],
    games: [game],
  };
  return { season, game };
}

function render(game, season, options) {
  const el = makeElement();
  renderGameDetail(el, season, game, options || {});
  return el.innerHTML;
}

test("a row with something to do opens in place, marked by a large chevron", () => {
  const { season, game } = sheet();

  const html = render(game, season, { onRecordAbsence() {} });

  assert.match(html, /data-row-acts[\s\S]*?class="att-go" data-row-toggle/);
  assert.match(html, /class="row-acts"><button[^>]*data-mark-absent="Alice">請假</);
  assert.doesNotMatch(html, /data-act-sheet/);
});

test("no row offers more than two buttons", () => {
  const { season, game } = sheet({
    absences: [{ id: 5, player_name: "Alice", covered_by: "Dave", filled_by: "Dave" }],
    confirmed_drop_ins: [
      { id: 200, player_id: 9, player_name: "Dave", covering: "Alice", linked: false },
    ],
    waitlist_entries: [{ id: 300, player_name: "Carol" }],
  });

  const html = render(game, season, {
    onRecordAbsence() {},
    onRecordAbsenceWithSubstitute() {},
    onAssignSubstitute() {},
    onCancelAbsence() {},
    onCancelSubstitute() {},
    onRemoveDropIn() {},
    onPromoteFromWaitlist() {},
    onLeaveWaitlist() {},
  });

  const groups = html.match(/<span class="row-acts">[\s\S]*?<\/span>/g) || [];
  assert.ok(groups.length >= 4);
  for (const g of groups) assert.ok((g.match(/<button/g) || []).length <= 2, g);
});

test("a sheet with nothing to change has no menus and no chevrons", () => {
  const { season, game } = sheet();

  const html = render(game, season, {});

  assert.doesNotMatch(html, /data-row-acts/);
  assert.doesNotMatch(html, /att-go/);
});

test("the member screen offers leave only on the viewer's own row", () => {
  const { season, game } = sheet();

  const html = render(game, season, {
    onRecordAbsence() {},
    canRecordAbsence: (m) => m.name === "Bob",
  });

  assert.match(html, /data-mark-absent="Bob"/);
  assert.doesNotMatch(html, /data-mark-absent="Alice"/);
});

test("the queue's buttons are 遞補 and 移除", () => {
  const { season, game } = sheet({
    waitlist_entries: [{ id: 300, player_name: "Carol", gender: "female" }],
  });

  const html = render(game, season, { onPromoteFromWaitlist() {}, onLeaveWaitlist() {} });

  assert.match(html, /data-promote-waitlist="300">遞補</);
  assert.match(html, /data-remove-waitlist="300">移除</);
});

test("取消請假 replaces 銷假, and 取消代打 waits in the substitute picker", () => {
  const { season, game } = sheet({
    absences: [{ id: 5, player_name: "Alice", covered_by: "Dave", filled_by: "Dave" }],
    confirmed_drop_ins: [
      { id: 200, player_id: 9, player_name: "Dave", covering: "Alice", linked: false },
    ],
  });

  const html = render(game, season, {
    onCancelAbsence() {},
    onAssignSubstitute() {},
    onCancelSubstitute() {},
  });

  const row = html.split('class="row-acts"')[1].split("</span>")[0];
  assert.match(row, /data-toggle-sub="5">更換代打</);
  assert.match(row, /data-undo-absence="5">取消請假</);
  assert.doesNotMatch(row, /取消代打/);
  assert.match(html, /data-sub-form="5"[\s\S]*?data-cancel-sub="200">取消代打</);
  assert.doesNotMatch(html, /銷假/);
});

test("指定代打 on a playing member is offered only where the page can do it in one step", () => {
  const { season, game } = sheet();
  const base = { onRecordAbsence() {}, onAssignSubstitute() {} };

  const offered = render(game, season, { ...base, onRecordAbsenceWithSubstitute() {} });
  const without = render(game, season, base);

  assert.match(offered, /data-absent-with-sub="Bob">指定代打</);
  assert.doesNotMatch(without, /data-absent-with-sub/);
});

test("confirming 請假並指定代打 sends who is away and who covers", () => {
  const { season, game } = sheet();
  const calls = [];
  const el = makeElement();
  renderGameDetail(el, season, game, {
    onRecordAbsence() {},
    onAssignSubstitute: () => calls.push("assign"),
    onRecordAbsenceWithSubstitute: (...args) => calls.push(args),
  });
  el.querySelector = (s) =>
    s === '[data-sub-name="new"]'
      ? { value: " Dave " }
      : s === '[data-sub-gender="new"]'
        ? { value: "male" }
        : null;
  const confirm = { dataset: { confirmSub: "new", confirmAbsent: "Bob" } };

  el.onclick({ target: { closest: (s) => (s === "[data-confirm-sub]" ? confirm : null) } });

  assert.deepEqual(calls, [["Bob", "Dave", "male", confirm]]);
});

test("a locked night offers no menus on the member screen", () => {
  const { season, game } = sheet({ past_deadline: true });

  const html = render(game, season, {
    asMember: true,
    onRecordAbsence() {},
    onRecordAbsenceWithSubstitute() {},
    onAssignSubstitute() {},
  });

  assert.doesNotMatch(html, /data-row-acts/);
});

test("the 出席 list is split into 固定成員 and 臨打, numbered straight through", () => {
  const { season, game } = sheet({
    confirmed_drop_ins: [
      { id: 200, player_id: 9, player_name: "Kevin", covering: null },
      { id: 201, player_id: 8, player_name: "Dave", covering: null, brought_by_name: "Bob" },
    ],
  });

  const html = render(game, season, {});

  assert.match(html, /att-sec"><span>固定成員<\/span><b>2<\/b>[\s\S]*Alice[\s\S]*Bob/);
  assert.match(html, /att-sec"><span>臨打<\/span><b>2<\/b>[\s\S]*att-num">3<[\s\S]*Kevin/);
  assert.doesNotMatch(html, /att-note">臨打</, "the heading says it once");
  assert.match(html, /Dave[\s\S]*?att-note by">Bob 報名</);
});

test("a list of members only needs no headings", () => {
  const { season, game } = sheet();
  assert.doesNotMatch(render(game, season, {}), /att-sec/);
});

test("the organizer's sheet names the game in one line, not a second card", () => {
  const { season, game } = sheet();

  const html = render(game, season, { compactHead: true });

  assert.match(html, /class="gd-head"/);
  assert.match(html, /每人 <strong>\$235<\/strong>/);
  assert.doesNotMatch(html, /class="hero"/);
});

test("請假並指定代打 is drawn with nobody taken off the queue", () => {
  const { season, game } = sheet({
    waitlist_entries: [
      { id: 300, player_id: 7, player_name: "Carol" },
      { id: 301, player_id: 8, player_name: "Erin" },
    ],
  });

  applyAbsenceWithSubstituteLocally(
    season,
    game.id,
    "Bob",
    { id: 55, substitute: { id: 301, player_id: 8, displaced_player_id: null } },
    { name: "Erin", gender: null }
  );

  assert.deepEqual(game.absences, [
    { id: 55, player_name: "Bob", covered_by: "Erin", filled_by: "Erin" },
  ]);
  assert.deepEqual(
    game.confirmed_drop_ins.map((d) => [d.player_name, d.covering]),
    [["Erin", "Bob"]]
  );
  assert.deepEqual(
    game.waitlist_entries.map((w) => w.player_name),
    ["Carol"],
    "Carol keeps her place"
  );
});
